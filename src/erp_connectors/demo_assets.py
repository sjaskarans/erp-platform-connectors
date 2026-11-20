import argparse
import json
import re
from pathlib import Path

from .demo_data import TABLES, configuration, seed_sql


def generate(catalog: str, schema: str, destination: Path, platform="databricks"):
    if platform not in {"databricks", "snowflake", "redshift"}:
        raise ValueError("Unsupported demo platform")
    if platform == "redshift":
        seed_sql(catalog, schema)  # Require safe names and a dedicated test schema.
        from .redshift import quote_identifier
        quote_identifier(catalog)
        quote_identifier(schema)
        from .demo_data import literal
        statements = ["-- Synthetic Redshift fixture: run as a setup owner in the configured database.",
                      "-- Stop on the first error; use a fresh schema, never replace existing client data.",
                      f'CREATE SCHEMA "{schema}";']
        for name, spec in TABLES.items():
            columns = ", ".join(f'"{column}" {"VARCHAR(256)" if kind == "STRING" else kind}' for column, kind in spec.columns)
            table = f'"{schema}"."{name}"'
            statements.append(f"CREATE TABLE {table} ({columns});")
            values = ["(" + ", ".join(literal(value, kind) for value, (_, kind) in zip(row, spec.columns)) + ")" for row in spec.rows]
            statements.append(f"INSERT INTO {table} VALUES\n" + ",\n".join(values) + ";")
        sql = "\n\n".join(statements) + "\n"
    elif platform == "snowflake":
        catalog, schema = catalog.upper(), schema.upper()
        # Validate with the same restricted naming rules before SQL generation.
        seed_sql(catalog, schema.lower())
        prefix = f'"{catalog}"."{schema}"'
        statements = ["-- Synthetic Snowflake fixture. Run as a setup owner, never the reader.",
                      "-- Stop on the first error. Use a fresh schema if a prior setup exists.",
                      f"CREATE SCHEMA {prefix} COMMENT='Synthetic ERP connector test data';"]
        from .demo_data import literal
        for name, spec in TABLES.items():
            columns = ", ".join(f'"{column.upper()}" {kind}' for column, kind in spec.columns)
            table = f'{prefix}."{name.upper()}"'
            statements.append(f"CREATE TABLE {table} ({columns});")
            values = ["(" + ", ".join(literal(value, kind) for value, (_, kind) in zip(row, spec.columns)) + ")" for row in spec.rows]
            statements.append(f"INSERT INTO {table} VALUES\n" + ",\n".join(values) + ";")
        sql = "\n\n".join(statements) + "\n"
    else:
        sql = seed_sql(catalog, schema)
    config = configuration(catalog, schema)
    if platform == "snowflake":
        for obj in config["objects"].values():
            obj["name"] = obj["name"].upper()
            obj["preview_columns"] = [column.upper() for column in obj["preview_columns"]]
        for query in config["queries"].values():
            query["statement"] = re.sub(r"`([^`]+)`", lambda match: '"' + match[1].upper() + '"', query["statement"])
            query["statement"] = re.sub(r":[a-z_]+", "?", query["statement"])
    destination.mkdir(parents=True, exist_ok=True)
    outputs = {
        "setup.sql": sql,
        "connector.demo.json": json.dumps(config, indent=2) + "\n",
        "expected.json": json.dumps({
            "synthetic": True, "as_of_date": "2026-11-18",
            "row_counts": {name: len(spec.rows) for name, spec in TABLES.items()},
            "company_1000": {
                "finance_by_currency": [["INR", "200.00"], ["USD", "50.00"]],
                "overdue_receivables": [["INR", "600.00"], ["USD", "50.00"]],
                "overdue_payables": [["INR", "300.00"]],
                "inventory_valuation": [["INR", "250.0000", "1"]],
                "open_sales_orders": [["INR", "1200.00"]],
                "open_purchase_orders": [["INR", "500.00"]],
            },
        }, indent=2) + "\n",
        "grants.template.sql": "-- Replace the principal placeholder; run as the setup owner.\n"
            + f"GRANT USE CATALOG ON CATALOG `{catalog}` TO `<reader-application-id>`;\n"
            + f"GRANT USE SCHEMA ON SCHEMA `{catalog}`.`{schema}` TO `<reader-application-id>`;\n"
            + "\n".join(f"GRANT SELECT ON TABLE `{catalog}`.`{schema}`.`{name}` TO `<reader-application-id>`;" for name in TABLES)
            + "\n-- Also grant CAN USE on the SQL warehouse through its permissions UI.\n",
    }
    if platform == "snowflake":
        outputs["grants.template.sql"] = (
            "-- Run after creating the ERP_CONNECTOR_READER role; see docs/snowflake-setup.md.\n"
            f'GRANT USAGE ON DATABASE "{catalog}" TO ROLE ERP_CONNECTOR_READER;\n'
            f'GRANT USAGE ON SCHEMA "{catalog}"."{schema}" TO ROLE ERP_CONNECTOR_READER;\n'
            + "\n".join(f'GRANT SELECT ON TABLE "{catalog}"."{schema}"."{name.upper()}" TO ROLE ERP_CONNECTOR_READER;' for name in TABLES)
            + "\n-- Grant USAGE on the selected warehouse separately.\n")
    if platform == "redshift":
        for query in config["queries"].values():
            query["statement"] = query["statement"].replace(f'`{catalog}`.`{schema}`.', f'"{schema}".').replace('`', '"')
            query["statement"] = query["statement"].replace(":as_of_date", "CAST(:as_of_date AS DATE)")
        config["objects"] = [{"name": name, "catalog": obj["catalog"], "schema_name": obj["schema"],
                              "table_name": obj["name"], "preview_columns": obj["preview_columns"]}
                             for name, obj in config["objects"].items()]
        config.update(platform="redshift", name="Redshift synthetic ERP", region="us-east-1", deployment="serverless",
                      database=catalog, workgroup_name="erp-test", aws_profile="erp-connector")
        outputs["connector.demo.json"] = json.dumps(config, indent=2) + "\n"
        outputs["grants.template.sql"] = (
            "-- Run as an administrator in the configured Redshift database.\n"
            "-- Replace IAMR:ERPConnectorRole with the exact database identity returned by the connection check.\n"
            "-- Create the role only if it does not already exist; stop on errors.\n"
            "CREATE ROLE erp_connector_reader;\n"
            f'GRANT USAGE ON SCHEMA "{schema}" TO ROLE erp_connector_reader;\n'
            + "\n".join(f'GRANT SELECT ON TABLE "{schema}"."{name}" TO ROLE erp_connector_reader;' for name in TABLES)
            + '\nGRANT ROLE erp_connector_reader TO "IAMR:ERPConnectorRole";\n'
            + "-- No CREATE/write privileges, future grants, or changes to PUBLIC are applied here.\n")
    for name, content in outputs.items():
        (destination / name).write_text(content, encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic connector setup assets; no cloud calls")
    parser.add_argument("--catalog", default="erp_demo")
    parser.add_argument("--schema", default="erp_connector_demo_v1")
    parser.add_argument("--platform", choices=["databricks", "snowflake", "redshift"], default="databricks")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    destination = args.out or Path(f"demo/{args.platform}" if args.platform != "databricks" else "demo/generated")
    generate(args.catalog, args.schema, destination, args.platform)
    print(f"Generated {args.platform} setup SQL, reader grants, connector config, and expected results in {destination}")


if __name__ == "__main__":
    main()
