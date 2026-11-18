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


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ERP platform connector")
    parser.add_argument("--config", default="connector.local.json")
    parser.add_argument("--prompt-secret", action="store_true",
                        help="Securely prompt for the OAuth M2M secret for this command")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("discover")
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
    try:
        secret = prompt_client_secret() if args.prompt_secret else None
        connector = load_connector(args.config, client_secret=secret)
        if args.command == "check":
            output = asdict(connector.check_connection())
        elif args.command == "discover":
            output = {name: asdict(result) for name, result in connector.discover().items()}
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


if __name__ == "__main__":
    raise SystemExit(main())
