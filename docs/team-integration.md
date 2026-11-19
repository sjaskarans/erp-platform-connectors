# Connector handoff: v0.2.0

This repository delivers the connector layer for integration into the larger
application. Each client installation has its own configuration and credentials.
The Next.js screen and loopback FastAPI services are reference clients/adapters.
The Python connector classes can be used directly by the team's existing backend.

## What is implemented

| Capability | Snowflake | Google Cloud Databricks |
| --- | --- | --- |
| Service identity | RSA key-pair, TYPE=SERVICE | Service principal, OAuth M2M |
| Connection check | Implemented and live verified | Implemented; SDK contract tested offline |
| Browse databases, schemas, tables/views, columns | Implemented and live verified | Not implemented; approved-object configuration remains |
| Describe approved objects and preview approved columns | Implemented and live verified | Implemented; tested offline |
| Registered parameterized SELECT templates | Implemented; driver behavior tested | Implemented; SDK behavior tested |
| Local Windows encrypted profiles | Included | Included |

SAP and Redshift connectors are not implemented in this release. Business
semantics, joins, AI decisions, visualization, application sign-in, and user
authorization belong to the larger application.

## Install and run

Python 3.11+ is required. Install the wheel supplied with this handoff, or install
the checked-out repository with `python -m pip install .`. The SDK needs no Next.js
server. The development reference screen additionally needs Node.js and frontend
dependencies:

```powershell
python -m venv .venv
& .venv/Scripts/python.exe -m pip install -e .
npm.cmd --prefix frontend ci
& .venv/Scripts/python.exe -m erp_connectors.demo --ui
```

Open `http://127.0.0.1:3000`. The gateway and service bind to loopback on ports
8000 and 8001. The launcher generates a temporary bearer token for service calls.
The reference UI keeps it server-side. Use `/docs` on the gateway for interactive
API documentation; `docs/openapi.json` is the checked-in service contract.

The sample-data panel runs a separate local fixture. A saved Snowflake profile
always uses that profile's real account and service identity.

## Client workflow without typing table names

1. The client enters account, warehouse, and dedicated service identity names.
2. Their administrator chooses the access boundary. The schema-discovery option
   generates explicit grants for all **current** tables/views in one approved
   schema. It grants no future objects. The individual-object option remains
   available when a client requires narrower source permissions.
3. The administrator reviews and runs the generated public-key registration and
   grants. The reference backend generates and encrypts the private key; it is
   never returned by the profile/setup APIs.
4. Test authentication. An identity with no selected objects is `connected`, not
   `verified`. The client can now browse visible metadata.
5. Browse databases → schemas → tables/views → columns, select the required
   objects, and choose preview columns. Search and pagination avoid downloading
   an entire large catalog. No business rows are retrieved by browsing.
6. Save the versioned selection and test object access. Use Preview to fetch a
   bounded set of rows from the selected object.

Discovery does not grant access and does not automatically approve every object.
Snowflake's grants are the source security boundary. The selected objects/columns
are an additional application allowlist. Deselecting a table in the app does not
revoke the service role's existing Snowflake privileges. New objects are never
automatically added to the app's selection.

## Python SDK integration

```python
from erp_connectors import DataObject, SnowflakeConnector

# Your backend supplies a factory using its client-specific service identity,
# secret provider, explicit warehouse/role, qmark parameter style, and timeouts.
# Return a NEW official Snowflake connection per call; the connector closes it.
connector = SnowflakeConnector(connection_factory=my_client_connection_factory,
                               objects={})

page = connector.browse("databases")
schemas = connector.browse("schemas", database="ERP_DEMO")
tables = connector.browse("objects", database="ERP_DEMO",
                          schema_name="ERP_CONNECTOR_DEMO_V1")
columns = connector.browse("columns", database="ERP_DEMO",
                           schema_name="ERP_CONNECTOR_DEMO_V1",
                           table="FINANCE_LINES")

# Build a fresh connector from the selection persisted by the application.
approved = {"finance": DataObject("ERP_DEMO", "ERP_CONNECTOR_DEMO_V1",
                                 "FINANCE_LINES", ("AMOUNT", "CURRENCY"))}
connector = SnowflakeConnector(my_client_connection_factory, approved)
result = connector.preview("finance", limit=5)
```

`browse()` returns `MetadataPage`: `items`, `next_cursor`, and
`prefix_case_sensitive`. Pass `after=page["next_cursor"]` to get the next page.
Keep the same level, parents, and prefix during pagination. Pages contain at most
100 items. Prefix matching is case sensitive. Names returned by the source must
be preserved exactly, including quoted mixed-case identifiers.

`check_connection()`, `describe()`, `preview()`, and registered-template
`execute()` return `QueryResult`. Rows contain strings or nulls; use the supplied
source type/precision/scale metadata. Parse financial amounts as Decimal, not
binary float. The connector raises sanitized `ConnectorError` codes on source
errors, timeouts, incomplete results, and row/byte budget violations. Never treat
a failed result as a zero total.

Analytical reads use registered, reviewed query templates, rather than a preview
or arbitrary client SQL. For example, provide `queries` when building the SDK:

```python
from erp_connectors import Parameter, QueryTemplate

queries = {"finance_total": QueryTemplate(
    'SELECT CURRENCY, SUM(AMOUNT) AS TOTAL_AMOUNT '
    'FROM "ERP_DEMO"."ERP_CONNECTOR_DEMO_V1"."FINANCE_LINES" '
    'WHERE COMPANY_CODE = ? GROUP BY CURRENCY',
    (Parameter("company_code", "STRING"),),
)}
connector = SnowflakeConnector(my_client_connection_factory, approved, queries)
result = connector.execute("finance_total", {"company_code": "1000"})
```

See `connections.runtime_connector()` for the reference service-identity factory.
`ConnectionStore` accepts a `vault` with `protect(str)->bytes` and
`reveal(bytes)->str` if the host application supplies its own secret provider.
The default vault uses Windows DPAPI under the Windows user running the backend.
The core SDK itself has no dependency on Windows profile persistence.

## Reference HTTP contract

All endpoints below require the service bearer token. The team's application must
authorize the operator and connection before forwarding requests. The reference
token is not a replacement for business-user authentication or authorization.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/connections` | Save account/identity settings; generate a Snowflake key if omitted |
| `GET /v1/connections` | Public profile metadata, versions, statuses; no credentials |
| `GET /v1/connections/{id}/setup` | Public key, fingerprint, administrator SQL, read-only diagnostic SQL |
| `POST /v1/connections/{id}/test` | Test login and selected-object metadata/preview access |
| `POST /v1/connections/{id}/browse` | Read a page of metadata; never business rows |
| `POST /v1/connections/{id}/selection` | Validate and save selected objects using a version check |
| `GET /v1/connections/{id}/objects/{label}/preview?limit=5` | Read selected columns only |
| `PUT /v1/connections/{id}` | Replace profile settings, retaining credentials if omitted |
| `DELETE /v1/connections/{id}` | Remove the local profile; does not revoke source access |

The per-profile HTTP reference currently exposes metadata selection and preview.
Use the SDK's registered-template execution from the larger application's query
worker, or wrap it in an authorized application route. Root `/v1/queries/...`
and `/v1/check` operate on the launcher's configured demo/single connector; they
are not analytical query/check routes for a saved profile. Do not substitute the
local sample-data query result for a client's source result.

Example browse request:

```json
{"level":"objects","database":"ERP_DEMO","schema_name":"ERP_CONNECTOR_DEMO_V1","prefix":"","after":""}
```

Example selection request (use the profile's latest version):

```json
{"version":1,"objects":[{"name":"finance","catalog":"ERP_DEMO","schema_name":"ERP_CONNECTOR_DEMO_V1","table_name":"FINANCE_LINES","object_kind":"TABLE","preview_columns":["AMOUNT","CURRENCY"]}]}
```

The selection endpoint checks object/column visibility before saving, preserves
the key and identity, increments the version, and resets test status. A stale
version returns 409; reload and reconcile the selection. No raw SQL, identity
fields, or credentials are accepted by this endpoint. Empty selection is allowed
for initial onboarding and metadata-only discovery.

Profile state progression is `saved` → `connected` (no objects selected), or
`saved` → `verified` (selected metadata and configured previews checked). Failed
tests produce `failed` with a sanitized error code. Objects without preview
columns are checked for metadata visibility only, not proof of SELECT access.

## Limits and integration responsibilities

- The reference profile/API supports 30 selected objects and 30 preview columns
  per object. Larger selections need deliberate API/result-budget changes or
  multiple profiles. The direct SDK's approved registry has no 30-object limit.
- Preview limit: 1–100 rows. Default SDK result budgets: 1,000 rows and 4 MB.
  Budget violations fail rather than returning partial analytical results.
- Operation timeout defaults to 120 seconds. Network/login timeouts are separate;
  multiple selected objects can make an HTTP verification exceed gateway limits.
  The host should run large verification jobs in its worker/job system.
- Discovery currently includes base tables and views. Other Snowflake object
  types need explicit support and tests.
- Metadata reflects currently visible objects; schema changes can occur between
  selection and execution. Source access and result validation apply on every read.
- Store client profiles/credentials separately for each installation. Do not
  reuse a client's account, role, private key, or profile database for another.
- Bind the reference service to loopback. For deployment beyond that boundary,
  the host owns authentication, connection-level authorization, TLS, secret
  rotation, network policies, auditing/retention, and request/job controls.
- Windows DPAPI storage is tied to the Windows identity and machine/profile.
  Plan backup/recovery and service-user migration before client deployment.
- Operators must approve trusted query templates and source grants. Parameter
  binding is not a general SQL sandbox, and user-provided SQL must not be placed
  into the template registry.

## Verification and delivery

Run `python -m unittest discover -s tests`; run `npm.cmd --prefix frontend run build`
when changing the reference UI. Tests cover async metadata initialization,
financial precision, bounded reads, injection/binding, discovery pagination,
secret redaction, selection validation/version races, and gateway behavior.
Live Snowflake validation uses the pilot's actual service identity. Databricks
still requires a client workspace live smoke test before deployment.

Build a reusable wheel with:

```powershell
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist
```

Share the private repository/release commit, wheel, this guide, and OpenAPI
contract with the team. Never share `.local-connections`, private keys, tokens,
client connection exports, or local logs. The package is ready for the team's
integration and acceptance testing; this reference application is not a complete
production security boundary for their larger application.
