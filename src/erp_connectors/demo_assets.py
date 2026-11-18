import argparse
import json
from pathlib import Path

from .demo_data import TABLES, configuration, seed_sql


def generate(catalog: str, schema: str, destination: Path):
    sql = seed_sql(catalog, schema)
    destination.mkdir(parents=True, exist_ok=True)
    outputs = {
        "setup.sql": sql,
        "connector.demo.json": json.dumps(configuration(catalog, schema), indent=2) + "\n",
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
    for name, content in outputs.items():
        (destination / name).write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic Databricks setup assets; no cloud calls")
    parser.add_argument("--catalog", default="erp_demo")
    parser.add_argument("--schema", default="erp_connector_demo_v1")
    parser.add_argument("--out", type=Path, default=Path("demo/generated"))
    args = parser.parse_args()
    generate(args.catalog, args.schema, args.out)
    print(f"Generated setup SQL, reader grants, connector config, and expected results in {args.out}")


if __name__ == "__main__":
    main()
