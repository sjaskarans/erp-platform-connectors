# Run the synthetic ERP API demo locally

No Databricks account, Google billing, cloud credentials, or Docker is needed.
The Python environment in this workspace already has the required libraries.

## Start the services

To include the Next.js connection setup screen, add `--ui` to the launcher
command below and open `http://127.0.0.1:3000`. The UI includes **Explore sample
data** and does not require entering the local API token. See
[the connection-screen guide](connection-ui.md) for customer-managed profiles.

Open PowerShell in this project folder:

```powershell
$env:ERP_CONNECTOR_MODE = 'local-demo'
& .venv/Scripts/python.exe -m erp_connectors.demo
```

This starts two real Python processes:

- API gateway: `http://127.0.0.1:8000`
- Connector service: `http://127.0.0.1:8001`

The launcher prints a randomly generated local API token. Keep the terminal
open. Wait for both startup messages, then open
[the interactive API documentation](http://127.0.0.1:8000/docs).
Click **Authorize**, paste the token, and click **Authorize** again. Enter the
token value only; Swagger adds the Bearer prefix.

Press Ctrl+C in the launcher terminal to stop both processes. Starting again
recreates the synthetic database and normally generates a new API token. No
existing company database is connected or modified. If a port is occupied,
choose unused ports with `ERP_GATEWAY_PORT` and `ERP_CONNECTOR_PORT`; do not stop
an unrelated process just to reclaim its port.

## Try the endpoints in order

1. `GET /health`: confirms the mode is `local-demo`; it does not check cloud access.
2. `GET /v1/check`: executes a SQL connection check.
3. `GET /v1/objects`: lists the nine approved objects.
4. `GET /v1/objects/finance_lines/schema`: shows source column metadata.
5. `GET /v1/objects/finance_lines/preview?limit=5`: fetches synthetic rows.
6. `POST /v1/queries/overdue_receivables`: execute with this body:

```json
{
  "parameters": {
    "company_code": "1000",
    "as_of_date": "2026-11-18"
  }
}
```

Expected rows are `INR: 600.00` and `USD: 50.00`. The disputed INR 400 invoice,
future INR 200 invoice, and other companies are excluded. Currency totals remain
separate. These are fixture-specific test rules, not a complete semantic pack.

The result identifies its platform as `local-duckdb-demo`. Its `local-...` query
references are local identifiers, not Databricks statement IDs.

## Synthetic dataset

| Object | Rows | Purpose |
| --- | ---: | --- |
| companies | 3 | SAP-like and Dynamics-like source identities, INR/USD/GBP |
| customers | 3 | Leading-zero IDs; same customer ID in different companies |
| suppliers | 2 | Company-scoped supplier keys |
| finance_lines | 6 | Signed ledger amounts and reversals, mixed currencies |
| receivables | 7 | Overdue, future, paid, and disputed invoices |
| payables | 3 | Overdue and future supplier balances |
| inventory_positions | 4 | Stock valuation and a missing unit cost |
| sales_orders | 3 | Open and canceled orders |
| purchase_orders | 2 | Open purchase orders |

All records are fictional; this is not an SAP-certified or Dynamics-certified
schema. Finance lines are an illustrative subset, not a complete balanced ledger.
The missing inventory cost is counted explicitly so a partial known valuation
cannot be silently interpreted as a complete valuation.

Other registered query IDs are `finance_by_currency`, `overdue_payables`,
`inventory_valuation`, `open_sales_orders`, and `open_purchase_orders`. All require
`company_code`; overdue queries also require `as_of_date`. See
`demo/generated/expected.json` for reference results for company 1000.

## What the tests establish

The automated suite exercises real SQL against DuckDB and HTTP through both
processes. It verifies query values, currency separation, scoped companies,
parameter binding, authentication, limits, and rejection of raw SQL request bodies.
Separate simulated-response tests exercise the Databricks statement lifecycle.

This local demo cannot establish actual Databricks authentication, permission
denials, cloud connectivity, compute cost, or complete dialect compatibility.
It has no tenant isolation, SSO, rate limits, durable jobs, persistent result
store, or automatic process restart. Requests are synchronous and intended only
for small local tests. Do not expose these ports beyond your machine.

## Move the fixture to real Databricks

After creating a workspace and choosing an existing catalog, generate assets
with the actual catalog name:

```powershell
& .venv/Scripts/python.exe -m erp_connectors.demo_assets --catalog YOUR_CATALOG --schema erp_connector_demo_v1
```

Run `demo/generated/setup.sql` in Databricks SQL as a setup identity with creation
permissions. It creates a fresh dedicated schema and tables, then inserts the
33 rows. It creates no catalog, storage bucket, workspace, or identity.

The script deliberately uses no DROP, REPLACE, or IF NOT EXISTS. Stop on the
first error; rerunning INSERT statements would duplicate data. If the schema
already exists or setup partially failed, use a fresh demo schema name such as
`erp_connector_demo_v2` and regenerate all assets. Have its owner clean up old
test schemas deliberately after you no longer need them.

Replace the principal placeholder in `grants.template.sql` with the runtime
principal's application ID and apply those read-only grants as the owner. Copy
`connector.demo.json` to your ignored `connector.local.json` only after reviewing
any existing local configuration. Use [the local Databricks authentication
walkthrough](local-databricks.md) for the first live check, then compare the same
fixture queries with `expected.json`. Numeric scale presentation can differ;
compare decimal values and types, not binary floats or formatted strings alone.
