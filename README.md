# ERP platform connectors

Python connector implementation for the existing ERP data used by the governed
intelligence platform. Implemented targets are **Google Cloud Databricks with
Unity Catalog**, **Snowflake with service-user RSA key-pair authentication**,
**Amazon Redshift through the IAM-authenticated Data API**, and **SAP via
read-only OData v2/v4 APIs**. SAP product/API availability and inbound identity
setup are client-specific; see [SAP setup](docs/sap-setup.md).

The connector retrieves metadata and data. Metric definitions, join inference,
semantic compilation, AI, dashboards, and ERP write-back are outside this package.

## Team handoff

Start with [the v0.4.0 integration guide](docs/team-integration.md) and
[the OpenAPI contract](docs/openapi.json). The reusable Python SDK is the
connector deliverable; the local Next.js screen and API processes demonstrate
how the team's larger application can call it.

Snowflake, Redshift and SAP support paginated metadata discovery and versioned object/column
selection. Clients can browse and choose accessible tables without typing each
object name. An administrator still approves the source access boundary once.
Databricks currently retains its approved-object configuration flow.

## Connector capabilities

- Explicit service-principal OAuth authentication through the official SDK.
- Snowflake service-user key-pair authentication through the official Python driver.
- Redshift Serverless/provisioned support through boto3, using temporary IAM
  credentials, named parameters, complete result pagination, and bounded reads.
- SAP OData metadata/entity discovery, explicit scalar projections, typed registered
  filters, bounded paging and exact numeric values through an authenticated HTTP session.
- Connection checks against the configured source (SAP checks metadata access).
- Column metadata for approved objects, including source types. Comments are
  included by the Databricks and Snowflake adapters.
- Optional previews restricted to explicitly approved columns.
- Execution of operator-reviewed, parameterized query templates.
- Asynchronous statement polling, complete chunk retrieval, timeout cancellation.
- Row/byte budgets: incomplete or truncated results fail instead of returning
  misleading totals.
- String/null result cells with source column type metadata retained.
  Consumers must parse DECIMAL using decimal arithmetic, never binary floats.
- Warehouse adapter structured execution logs containing statement IDs, SQL fingerprints, timing,
  and outcome; query text, parameter values, and result rows are excluded.

The package includes a command-line tool and two separate local HTTP test
processes: a gateway and a connector service. Their shared bearer token is for
single-operator testing, not production user authorization. The Next.js connection
screen calls the shared API through a server-side proxy. Secrets entered in its
setup form are sent to the backend for encrypted storage; they are never retained
in browser storage or returned by the profile API. The browser cannot submit
arbitrary SQL through these endpoints.

## Test immediately without a Databricks account

For a user-facing setup screen, run `python -m erp_connectors.demo --ui` with the
project's Python environment. Open `http://127.0.0.1:3000` to add, edit, test, or
preview your own named connections. See [the connection UI guide](docs/connection-ui.md).
Select Databricks, Snowflake, Amazon Redshift, or SAP OData when adding a connection. For SAP identities, service approval, and offline fixtures, use [SAP setup](docs/sap-setup.md). For Redshift IAM roles, policies, and synthetic test data, use [the Redshift setup guide](docs/redshift-setup.md). For Snowflake customer onboarding, the app generates and protects its private key and displays public-key setup SQL. If your ERP data is already loaded, follow [customer testing](docs/snowflake-customer-testing.md). Snowflake's identity,
permissions, CLI, and test-data setup are covered in [the Snowflake guide](docs/snowflake-setup.md).
Saved profiles use Windows-encrypted secrets and are local to this deployment;
the reference API/UI is intended for integration into the larger application. Redshift profiles contain only non-secret target/provider settings.

On a fresh checkout, install the Python package and the frontend dependencies:

```powershell
python -m venv .venv
& .venv/Scripts/python.exe -m pip install -e .
npm.cmd --prefix frontend ci
& .venv/Scripts/python.exe -m erp_connectors.demo --ui
```

Use a normal Windows terminal for encrypted connection-profile storage. The local
data demo is available without Databricks credentials or cloud billing.

The [local API demo](docs/testing-demo.md) seeds nine fictional ERP tables with
33 rows into an in-memory DuckDB database. Start it with:

```powershell
$env:ERP_CONNECTOR_MODE = 'local-demo'
& .venv/Scripts/python.exe -m erp_connectors.demo
```

The terminal prints the API documentation URL and a temporary local API token.
Both services bind only to `127.0.0.1`. This mode makes no Databricks calls and
needs no Google billing or cloud credentials. It tests real SQL and HTTP, but
does not validate Databricks authentication, SQL dialect behavior, or permissions.

For actual Google Cloud Databricks, follow
[the account/workspace setup path](docs/cloud-from-scratch.md). Generated assets
in `demo/generated` contain synthetic data setup SQL, reader grants, connector
configuration, and expected results; none has been applied to a cloud workspace.

## Start

For the confirmed **local Windows → Google Cloud Databricks** setup, follow
[the local walkthrough](docs/local-databricks.md). It includes the hidden OAuth
secret prompt and the first connection check.

Use Python 3.11 or newer:

```powershell
python -m venv .venv
& .venv/Scripts/python.exe -m pip install -e .
Copy-Item connector.example.json connector.local.json
```

Follow [the Databricks setup guide](docs/databricks-setup.md) to create the service
identity, grant access, and set environment variables. Edit the local JSON to
match actual approved views and columns; the example names are fictional.

```powershell
& .venv/Scripts/erp-connector.exe check
& .venv/Scripts/erp-connector.exe discover
& .venv/Scripts/erp-connector.exe describe finance_lines
& .venv/Scripts/erp-connector.exe preview finance_lines --limit 10
& .venv/Scripts/erp-connector.exe query finance_by_currency --parameters '{"company_code":"1000"}'
```

Query data goes to stdout and audit events to stderr. Both may be captured by
your terminal or process supervisor. Keep result files and terminal captures in
approved storage with appropriate access and retention.

`check` proves authentication and SQL compute access. `describe` and `preview`
exercise object access separately. Empty metadata can mean an object is absent
or not visible to this principal; it is not proof that a table does not exist.

## Configuration and trust boundary

`connector.local.json` is a trusted operator-managed deployment artifact.
Callers choose a registered query ID and supply bound values; they cannot supply
SQL, catalogs, object names, or column names through the command arguments.

The query registry must contain reviewed, read-only SELECT queries against
approved objects. It is **not** a semantic pack or a SQL sandbox. Read-only
warehouse grants, inherited-group review, function privileges, and appropriate
data policies remain the enforcement boundary. The object registry scopes
discovery and previews; it does not parse or constrain registered SQL templates.

Do not turn off database access policies or give this identity broad privileges
to make a query work. Use approved views that implement the required masking and
row restrictions. An upstream service must enforce user and company access;
the connector's shared principal does not convey the end user's identity.

The interface in `models.py` is the starting contract for later platform
implementations. Discovery is scoped to approved objects, not an unrestricted
catalog crawl. Query-history mining, data profiling, and extra metadata sources
require separate approval and permission design in later iterations.

## Reliability limits

- The application does not replay a failed submission. The official SDK can
  retry requests internally. A submission failure without a statement ID has an
  unknown outcome: reconcile in Databricks before retrying. Do not assume
  exactly-once execution.
- Timeouts include the polling/result phase but are not hard wall-clock limits:
  an SDK network call or internal retry can exceed the remaining deadline.
- Cancellation is best effort. Audit records distinguish a request from a
  failed cancellation request. Confirm terminal state in Databricks and use
  warehouse-side controls for resource protection.
- No durable job store or restart recovery is implemented yet. If the process
  crashes, use the logged statement ID to reconcile or cancel execution.
- Bounded INLINE results are intended for semantic queries and previews, not
  bulk extraction. Result limits do not limit the amount of data scanned by SQL.
- `fetched_at` is a connector retrieval timestamp, **not** source-data freshness.
  Actual ingestion watermarks must come from the customer's data pipeline.
- Logs provide execution references, not full semantic lineage. Persist the
  caller identity, authorization decision, semantic version, and approved
  source mapping in the upstream service.

## Tests

```powershell
& .venv/Scripts/python.exe -m unittest discover -s tests -v
```

Tests simulate platform responses to verify binding, polling, result chunks,
type preservation, limits, errors, cancellation, and audit redaction. They do not
prove live authentication, network access, Unity Catalog permissions, or business
correctness. Complete the live acceptance checks in the setup guide.

The Databricks SDK is pinned to the version exercised by the offline HTTP
contract test. Re-run the checks when upgrading it; resolve and lock transitive
dependencies in the target deployment's normal build process.

## Project context

Scope derived from the supplied document's **Detailed Semantic Layer** tab,
especially sections 4.1–4.5 and 9.4: read-only platform access; schema metadata
for the context engine; validated query execution for the future compiler;
execution references for auditing. The document's market, regulatory, and
competitive claims have not been independently validated for this implementation.

[Source document](https://docs.google.com/document/d/1MeaDhmnCS4ASTR54PwELs1HTeTloY253rwNG9VHm-JM/edit?tab=t.vtdx47icdddj)
