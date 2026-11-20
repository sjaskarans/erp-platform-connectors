import argparse
import getpass
import json
import logging
import os
import warnings
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from .databricks import DatabricksConnector
from .models import ConnectorError, DataObject, Parameter, QueryTemplate


def prompt_client_secret() -> str:
    """Read a secret without echo; refuse getpass's insecure echoing fallback."""
    if os.environ.get("DATABRICKS_AUTH_TYPE") != "oauth-m2m":
        raise ValueError("The secret prompt requires oauth-m2m authentication")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        secret = getpass.getpass("Databricks OAuth secret (hidden): ")
    if not secret:
        raise ValueError("OAuth secret must not be empty")
    return secret


def load_connector(path: str, *, client_secret: str | None = None) -> DatabricksConnector:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config

    config = json.loads(Path(path).read_text(encoding="utf-8"))
    host = os.environ["DATABRICKS_HOST"]
    parsed = urlparse(host)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("DATABRICKS_HOST must be an HTTPS workspace origin")
    auth_type = os.environ.get("DATABRICKS_AUTH_TYPE", "")
    if auth_type not in {"oauth-m2m", "env-oidc", "file-oidc"}:
        raise ValueError("Explicit service-principal OAuth authentication is required")
    if not os.environ.get("DATABRICKS_CLIENT_ID"):
        raise ValueError("DATABRICKS_CLIENT_ID is required")
    if client_secret is not None and (auth_type != "oauth-m2m" or not client_secret):
        raise ValueError("A nonempty secret is valid only with oauth-m2m")
    auth_options = {"client_secret": client_secret} if client_secret is not None else {}
    # Explicit auth_type prevents fallback to a developer's PAT or interactive login.
    client = WorkspaceClient(config=Config(
        host=host, auth_type=auth_type,
        http_timeout_seconds=15, retry_timeout_seconds=15,
        **auth_options,
    ))
    objects = {name: DataObject(**{**value, "preview_columns": tuple(value.get("preview_columns", []))})
               for name, value in config["objects"].items()}
    queries = {name: QueryTemplate(
        statement=value["statement"],
        parameters=tuple(Parameter(**p) for p in value.get("parameters", [])),
    ) for name, value in config.get("queries", {}).items()}
    return DatabricksConnector(
        client.api_client, os.environ["DATABRICKS_SQL_WAREHOUSE_ID"],
        objects, queries, **config.get("limits", {}),
    )


def load_snowflake_connector(path: str, *, passphrase: str | None = None):
    from .connections import SECRET_FIELDS, SnowflakeConnectionInput, runtime_connector
    from .snowflake import SnowflakeConnector
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    pem = Path(os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]).read_text(encoding="utf-8")
    password = passphrase if passphrase is not None else os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    profile = SnowflakeConnectionInput(
        platform="snowflake", name="CLI Snowflake", account=os.environ["SNOWFLAKE_ACCOUNT"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"], username=os.environ["SNOWFLAKE_USER"],
        role=os.environ["SNOWFLAKE_ROLE"], private_key_pem=pem, private_key_passphrase=password,
        objects=[{"name": name, "catalog": obj["catalog"], "schema_name": obj["schema"],
                  "table_name": obj["name"], "preview_columns": obj.get("preview_columns", [])}
                 for name, obj in config["objects"].items()],
    )
    live = runtime_connector(profile.model_dump(exclude=SECRET_FIELDS),
                             json.dumps({"private_key_pem": pem, "private_key_passphrase": password}))
    queries = {name: QueryTemplate(value["statement"], tuple(Parameter(**p) for p in value.get("parameters", [])))
               for name, value in config.get("queries", {}).items()}
    return SnowflakeConnector(live.connection_factory, live.objects, queries, **config.get("limits", {}))


def load_redshift_connector(path: str):
    from .connections import RedshiftConnectionInput, runtime_connector
    from .redshift import RedshiftConnector
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    profile = RedshiftConnectionInput.model_validate({key: value for key, value in config.items() if key not in {"queries", "limits"}})
    live = runtime_connector(profile.model_dump(), "")
    queries = {name: QueryTemplate(value["statement"], tuple(Parameter(**p) for p in value.get("parameters", [])))
               for name, value in config.get("queries", {}).items()}
    return RedshiftConnector(live.client, live.database, live.objects, queries,
        workgroup_name=profile.workgroup_name, cluster_identifier=profile.cluster_identifier, **config.get("limits", {}))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ERP platform connector")
    parser.add_argument("--config", default="connector.local.json")
    parser.add_argument("--platform", choices=["databricks", "snowflake", "redshift"], default="databricks")
    parser.add_argument("--prompt-secret", action="store_true",
                        help="Prompt for the Databricks OAuth secret or Snowflake key passphrase")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("discover")
    browse = commands.add_parser("browse")
    browse.add_argument("level", choices=["databases", "schemas", "objects", "columns"])
    browse.add_argument("--database", default="")
    browse.add_argument("--schema", default="")
    browse.add_argument("--table", default="")
    browse.add_argument("--prefix", default="")
    browse.add_argument("--after", default="")
    describe = commands.add_parser("describe")
    describe.add_argument("object_id")
    preview = commands.add_parser("preview")
    preview.add_argument("object_id")
    preview.add_argument("--limit", type=int, default=20)
    query = commands.add_parser("query")
    query.add_argument("query_id")
    query.add_argument("--parameters", default="{}", help="JSON object of strings or null")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("erp_connectors").setLevel(logging.INFO)
    connector = None
    try:
        if args.platform == "redshift":
            if args.prompt_secret:
                raise ValueError("Redshift uses the AWS credential provider, not a password prompt")
            connector = load_redshift_connector(args.config)
        elif args.platform == "snowflake":
            password = None
            if args.prompt_secret:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    password = getpass.getpass("Snowflake private-key passphrase (hidden): ")
            connector = load_snowflake_connector(args.config, passphrase=password)
        else:
            secret = prompt_client_secret() if args.prompt_secret else None
            connector = load_connector(args.config, client_secret=secret)
        if args.command == "check":
            output = asdict(connector.check_connection())
        elif args.command == "discover":
            output = {name: asdict(result) for name, result in connector.discover().items()}
        elif args.command == "browse":
            output = connector.browse(args.level, database=args.database or getattr(connector, "database", ""),
                schema_name=args.schema, table=args.table, prefix=args.prefix, after=args.after)
        elif args.command == "describe":
            output = asdict(connector.describe(args.object_id))
        elif args.command == "preview":
            output = asdict(connector.preview(args.object_id, args.limit))
        else:
            values = json.loads(args.parameters)
            if not isinstance(values, dict):
                raise ValueError("Parameters must be a JSON object")
            output = asdict(connector.execute(args.query_id, values))
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except ConnectorError as error:
        print(json.dumps({"error": error.code, "statement_id": error.statement_id}))
        return 1
    except Exception:
        # Do not print SDK exception text, credentials, configuration, or SQL.
        print(json.dumps({"error": "CONFIGURATION_OR_AUTHENTICATION_ERROR"}))
        return 1
    finally:
        close = getattr(connector, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
