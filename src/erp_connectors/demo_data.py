"""Deterministic, fictional ERP fixtures shared by local and cloud tests."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Table:
    columns: tuple[tuple[str, str], ...]
    rows: tuple[tuple, ...]


TABLES = {
    "companies": Table(
        (("company_code", "STRING"), ("source_system", "STRING"), ("name", "STRING"), ("currency", "STRING")),
        (("1000", "SAP_DEMO", "Demo India", "INR"), ("2000", "DYNAMICS_DEMO", "Demo US", "USD"),
         ("3000", "SAP_DEMO", "Demo UK", "GBP"))),
    "customers": Table(
        (("customer_id", "STRING"), ("company_code", "STRING"), ("name", "STRING")),
        (("000001", "1000", "Fictional India Customer"), ("000001", "2000", "Fictional US Customer"),
         ("000003", "3000", "Fictional UK Customer"))),
    "suppliers": Table(
        (("supplier_id", "STRING"), ("company_code", "STRING"), ("name", "STRING")),
        (("000010", "1000", "Fictional India Supplier"), ("000010", "2000", "Fictional US Supplier"))),
    "finance_lines": Table(
        (("company_code", "STRING"), ("fiscal_year", "INT"), ("document_id", "STRING"),
         ("account", "STRING"), ("amount", "DECIMAL(18,2)"), ("currency", "STRING"), ("posting_date", "DATE")),
        (("1000", 2026, "000001", "REVENUE", "1000.00", "INR", "2026-10-01"),
         ("1000", 2026, "000002", "REVENUE_REVERSAL", "-200.00", "INR", "2026-10-02"),
         ("1000", 2026, "000003", "COGS", "-600.00", "INR", "2026-10-03"),
         ("1000", 2026, "000004", "REVENUE", "50.00", "USD", "2026-10-03"),
         ("2000", 2026, "000001", "REVENUE", "2000.00", "USD", "2026-10-01"),
         ("2000", 2026, "000002", "REVENUE_REVERSAL", "-100.00", "USD", "2026-10-02"))),
    "receivables": Table(
        (("company_code", "STRING"), ("invoice_id", "STRING"), ("customer_id", "STRING"),
         ("due_date", "DATE"), ("outstanding_amount", "DECIMAL(18,2)"), ("currency", "STRING"), ("disputed", "BOOLEAN")),
        (("1000", "INV001", "000001", "2026-10-15", "600.00", "INR", False),
         ("1000", "INV002", "000001", "2026-10-20", "400.00", "INR", True),
         ("1000", "INV003", "000001", "2026-12-01", "200.00", "INR", False),
         ("1000", "INV004", "000001", "2026-10-15", "50.00", "USD", False),
         ("2000", "INV001", "000001", "2026-10-15", "800.00", "USD", False),
         ("2000", "INV002", "000001", "2026-10-15", "0.00", "USD", False),
         ("3000", "INV001", "000003", "2026-10-15", "250.00", "GBP", False))),
    "payables": Table(
        (("company_code", "STRING"), ("invoice_id", "STRING"), ("supplier_id", "STRING"),
         ("due_date", "DATE"), ("outstanding_amount", "DECIMAL(18,2)"), ("currency", "STRING")),
        (("1000", "BILL001", "000010", "2026-10-15", "300.00", "INR"),
         ("1000", "BILL002", "000010", "2026-12-01", "150.00", "INR"),
         ("2000", "BILL001", "000010", "2026-10-15", "500.00", "USD"))),
    "inventory_positions": Table(
        (("company_code", "STRING"), ("material_id", "STRING"), ("quantity", "DECIMAL(18,2)"),
         ("unit_cost", "DECIMAL(18,2)"), ("currency", "STRING")),
        (("1000", "000100", "10.00", "25.00", "INR"), ("1000", "000101", "2.00", None, "INR"),
         ("2000", "000100", "5.00", "40.00", "USD"), ("3000", "000100", "3.00", "10.00", "GBP"))),
    "sales_orders": Table(
        (("company_code", "STRING"), ("order_id", "STRING"), ("status", "STRING"),
         ("amount", "DECIMAL(18,2)"), ("currency", "STRING")),
        (("1000", "SO001", "OPEN", "1200.00", "INR"), ("1000", "SO002", "CANCELED", "400.00", "INR"),
         ("2000", "SO001", "OPEN", "1000.00", "USD"))),
    "purchase_orders": Table(
        (("company_code", "STRING"), ("order_id", "STRING"), ("status", "STRING"),
         ("amount", "DECIMAL(18,2)"), ("currency", "STRING")),
        (("1000", "PO001", "OPEN", "500.00", "INR"), ("2000", "PO001", "OPEN", "700.00", "USD"))),
}


def configuration(catalog: str, schema: str) -> dict:
    def table(name):
        return f'`{catalog}`.`{schema}`.`{name}`'
    queries = {
        "finance_by_currency": (f"SELECT currency, SUM(amount) AS amount FROM {table('finance_lines')} WHERE company_code = :company_code GROUP BY currency ORDER BY currency", []),
        "overdue_receivables": (f"SELECT currency, SUM(outstanding_amount) AS amount FROM {table('receivables')} WHERE company_code = :company_code AND due_date < :as_of_date AND outstanding_amount > 0 AND disputed = false GROUP BY currency ORDER BY currency", [("as_of_date", "DATE")]),
        "overdue_payables": (f"SELECT currency, SUM(outstanding_amount) AS amount FROM {table('payables')} WHERE company_code = :company_code AND due_date < :as_of_date AND outstanding_amount > 0 GROUP BY currency ORDER BY currency", [("as_of_date", "DATE")]),
        "inventory_valuation": (f"SELECT currency, SUM(quantity * unit_cost) AS known_value, SUM(CASE WHEN unit_cost IS NULL THEN 1 ELSE 0 END) AS missing_cost_rows FROM {table('inventory_positions')} WHERE company_code = :company_code GROUP BY currency ORDER BY currency", []),
        "open_sales_orders": (f"SELECT currency, SUM(amount) AS amount FROM {table('sales_orders')} WHERE company_code = :company_code AND status = 'OPEN' GROUP BY currency ORDER BY currency", []),
        "open_purchase_orders": (f"SELECT currency, SUM(amount) AS amount FROM {table('purchase_orders')} WHERE company_code = :company_code AND status = 'OPEN' GROUP BY currency ORDER BY currency", []),
    }
    return {
        "objects": {name: {"catalog": catalog, "schema": schema, "name": name,
                           "preview_columns": [column for column, _ in spec.columns]}
                    for name, spec in TABLES.items()},
        "queries": {name: {"statement": sql,
                           "parameters": [{"name": p, "type": t} for p, t in [("company_code", "STRING"), *params]]}
                    for name, (sql, params) in queries.items()},
        "limits": {"max_rows": 1000, "max_bytes": 4_000_000, "timeout_seconds": 120},
    }


def literal(value, kind: str) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if kind == "INT" or kind.startswith("DECIMAL"):
        return str(Decimal(str(value)))
    quoted = "'" + str(value).replace("'", "''") + "'"
    return f"DATE {quoted}" if kind == "DATE" else quoted


def seed_sql(catalog: str, schema: str) -> str:
    if not schema.startswith("erp_connector_demo_"):
        raise ValueError("Use a dedicated schema starting with erp_connector_demo_")
    def quote(value):
        if not value or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in value):
            raise ValueError("Demo catalog/schema names must use letters, digits, and underscores")
        return f"`{value}`"
    prefix = f"{quote(catalog)}.{quote(schema)}"
    statements = ["-- Synthetic demo only. Run as a setup user, never the read-only connector.",
                  "-- Creates new objects only. Stop on the first error; do not rerun into an existing schema.",
                  f"CREATE SCHEMA {prefix} COMMENT 'Synthetic ERP connector test data';"]
    for name, spec in TABLES.items():
        columns = ", ".join(f"{quote(column)} {kind}" for column, kind in spec.columns)
        statements.append(f"CREATE TABLE {prefix}.{quote(name)} ({columns}) USING DELTA COMMENT 'Fictional connector fixture: {name}';")
        rows = ["(" + ", ".join(literal(value, kind) for value, (_, kind) in zip(row, spec.columns)) + ")"
                for row in spec.rows]
        statements.append(f"INSERT INTO {prefix}.{quote(name)} VALUES\n" + ",\n".join(rows) + ";")
    return "\n\n".join(statements) + "\n"
