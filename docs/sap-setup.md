# SAP connector: client setup and team integration

The fourth adapter is **SAP OData v2/v4**, for released S/4HANA APIs or ECC /
S/4HANA Gateway services that expose XML `$metadata` and JSON entity collections.
It does not connect directly to SAP tables, HANA SQL, RFC/BAPI, SOAP, or Business
One Service Layer. An SAP administrator must confirm the product, edition,
service, version, authentication method and business authorizations first.

The reference profile/UI uses a dedicated **communication/technical user** with
Basic authentication over verified HTTPS, only where the chosen service permits
it. This is a service identity, rather than a person's everyday SAP login.
The Python SDK also accepts a host-managed authenticated `requests.Session` for
client certificates or managed token refresh. That integration is owned by the
host application; the reference form does not provision certificates or OAuth.
SAP inbound authentication is scenario-specific. Do not assume generic OAuth
client credentials works for every SAP API, or substitute instructions for
outbound OAuth configuration.

## 1. Select the business API

Have the client identify a released read API for each required business domain
in [SAP Business Accelerator Hub](https://api.sap.com/). Confirm availability for
their release, its communication scenario, supported inbound authentication,
entity sets, business restrictions and whether its license permits this usage.
One connection profile represents one service root; use multiple profiles for
different services. Existing SAP data already in Snowflake/Databricks/Redshift
can continue through those connectors without a direct SAP connection.

## 2. Create the service identity and source access

### S/4HANA Cloud Public Edition

1. An administrator opens **Maintain Communication Users** and creates a new
   dedicated inbound communication user, e.g. `SVC_ERP_CONNECTOR`. Generate its
   password through SAP if the selected scenario permits user/password auth.
   Store it in the client's approved secret manager. Never put it in Git or chat.
2. In **Communication Systems**, create a system representing this client
   installation. For an inbound-only scenario choose **Inbound Only** where
   applicable. Add the communication user under **Users for Inbound Communication**
   with the scenario-supported authentication method.
3. In **Communication Arrangements**, create an arrangement for the **actual
   scenario belonging to the chosen API**, select that system, inbound user and
   authentication method, and maintain scenario-specific restrictions. There is
   no single communication scenario granting all ERP read access.
4. Copy the exact URL from **Inbound Services**. Use its API host, not the Fiori
   browser URL. Remove `$metadata`, entity names and query parameters to obtain
   the service root. Confirm the correct OData version.
5. Have the SAP administrator review all permissions exposed by that scenario.
   Some standard scenarios include writes; a GET-only connector does not remove
   those source privileges. Where read-only source access is mandatory, use a
   suitable read-only scenario/service and supported business restrictions.
6. Test metadata and a small read for the required entity with this identity.
   Check that out-of-scope company codes/business data are denied as intended.

These steps follow SAP's [communication arrangement instructions](https://help.sap.com/docs/SAP_S4HANA_CLOUD/0f69f8fb28ac4bf48d2b57b9637e81fa/a0771f6765f54e1c8193ad8582a32edb.html)
and [inbound communication system guidance](https://help.sap.com/docs/SAP_S4HANA_CLOUD/0f69f8fb28ac4bf48d2b57b9637e81fa/1bfe32ae08074b7186e375ab425fb114.html).
SAP's [authentication setup guide](https://help.sap.com/doc/6ce62b6bdda340ffbeae3f138c3cb71b/SHIP/en-US/Set_Up_Authentication_for_SAP_S4HANA_Cloud_Extensions.pdf)
describes communication-user Basic authentication and other supported setups.

### S/4HANA Private Edition / on-premises or ECC Gateway

The client's Basis/security team supplies the enabled Gateway OData endpoint,
network route and a dedicated non-dialog technical identity following their
policy. They activate/register the exact service using the tools appropriate to
their installed Gateway version (commonly `/IWFND/MAINT_SERVICE` for v2; v4 has
different administration), configure HTTPS, and assign service plus business
authorizations through reviewed PFCG roles. Restrict read/display activities and
organizational values wherever the service supports them. Do not assign SAP_ALL
or copy an administrator's role. Validate actual access with authorization traces
and positive/negative reads; exact authorization objects depend on the service.
These are administrator tasks, not automatic grants performed by this package.
See SAP’s [Gateway activation and role guidance](https://help.sap.com/doc/61634ead9e5144b89e7eca2b1d4b8bce/1809.latest/en-US/UITECH_OP1809_latest.pdf)
for the appropriate installed release.
On-premises connectivity may require a corporate VPN/proxy or an approved BTP
destination/Cloud Connector integration. The SDK transport is injectable for that
host-managed integration; this package does not build a Cloud Connector tunnel.

## 3. Approve the destination on the backend

Before starting the local reference app, the operator configures an exact list
of trusted roots. Example placeholders must be replaced with the real service:

```powershell
$env:ERP_SAP_ALLOWED_SERVICE_ROOTS='["https://client-api.example/sap/opu/odata/sap/API_SERVICE/"]'
& .venv/Scripts/python.exe -m erp_connectors.demo --ui
```

Restart the running service after changing this setting. It belongs in trusted
backend deployment configuration, not a client-controlled API field. Root
approval occurs before constructing a credentialed HTTP session. HTTPS certificate
validation stays enabled. Pagination must stay on the same origin and exact
entity path; redirects and external metadata references are not followed.
The reference transport disables implicit `.netrc` and environment proxies; use
an explicitly configured SDK session for corporate trust bundles/proxies/mTLS.

## 4. Connect as a client

1. Open `http://127.0.0.1:3000`, choose **Add connection → SAP OData**.
2. Enter a friendly name, the approved service root, OData version, communication
   user and password. Optionally enter the three-digit SAP client supplied by
   the administrator; otherwise the service default applies. Choose **Save & test connection**.
3. An empty selection reports **Connected · choose data**. This only proves
   metadata access. Browse **Service → namespace → entities → scalar properties**.
   The shared API calls the service `catalog=service`, the EDM namespace
   `schema_name`, the entity set `table_name`, and `object_kind=ENTITY`.
4. Select the required entities and explicit preview fields, then save. No
   business rows are read by browsing or saving the selection.
5. Test again and choose **Preview**. A test reads one row for entities with
   preview fields; an entity selected without fields is metadata-checked only.
   Confirm company-code restrictions and values against SAP. Empty results can be
   valid; they do not prove access to every desired business record.

The API never returns the password. The Windows reference store encrypts it
with DPAPI for the current Windows account. Editing with a blank password keeps
the existing secret; changing the URL, SAP client or user requires re-entering it. Deleting
a local profile does not delete the SAP identity or revoke SAP permissions.
Your team's main backend should supply its own secret manager and lifecycle.

## 5. Integrate the Python SDK

```python
import requests
from erp_connectors import SAPConnector, DataObject, ODataQueryTemplate, ODataFilter

session = requests.Session()
session.trust_env = False
session.auth = (communication_user, password_from_secret_manager)
# For certificate authentication, configure session.cert on the host instead.
# For OAuth, the host supplies a supported SAP flow and refreshing AuthBase.
connector = SAPConnector(
    session, approved_service_root,
    {"finance": DataObject("service", actual_edm_namespace, actual_entity_set,
                           ("CompanyCode", "Amount", "Currency"))},
    {"company_finance": ODataQueryTemplate(
        "finance", ("CompanyCode", "Amount", "Currency"),
        (ODataFilter("CompanyCode", "eq", "company"),))},
    odata_version="2", owns_session=True,
)
try:
    result = connector.execute("company_finance", {"company": "1000"})
finally:
    connector.close()
```

Property names above illustrate the contract; use names from the client's live
metadata. SDK roots/sessions are trusted host inputs; apply your deployment's
destination allowlist before constructing the SDK directly. SDK calls do not read
the reference app's environment allowlist. Query registries are backend-owned.
Do not let callers create arbitrary projections, filters, URLs or credentials.
`execute` takes an `ODataQueryTemplate`, not warehouse SQL. Supported parameter
types: String, Boolean, integer primitives, Decimal, Guid, plus Date for v4;
operators: eq/ne/gt/ge/lt/le combined with AND. Null supports eq/ne only. Other
date/time literals, arbitrary expressions, joins, `$expand`, `$apply`, function
imports/actions, batches, delta feeds, complex/collection properties and writes
are not supported. The package does not aggregate ERP metrics or infer semantics.

Results retain source scalar strings (including v2 `/Date(...)` values), nulls,
leading zeroes and exact decimal lexical values. Columns include EDM types,
precision/scale, key flags, SAP property attributes and inline annotation
attributes for upstream mapping. External/complex annotation expressions are not
resolved. Keep company keys and currency/unit fields in semantic models; parse
money with Decimal. OData reads are not a transactionally consistent snapshot.
Use a source-supported extraction/CDC solution for replication guarantees.

Defaults are 10,000 rows, 8 MB per metadata/read operation, 100 data pages and a
120-second operation deadline, with bounded HTTP connect/read waits. Complete
registered-query reads fail on budget exhaustion instead of returning partial
totals. Preview reads up to the requested limit plus one to indicate truncation.
Metadata is cached only for the SDK instance; create a fresh instance to refresh.
No source messages, passwords, URLs with filter values or data rows are logged
by the adapter. Host HTTP debug logging must be configured appropriately too.

CLI: copy `connector.sap.example.json` to ignored `connector.local.json`, replace
the fixture names with real metadata and set the trusted root list. Then:

```powershell
& .venv/Scripts/python.exe -m erp_connectors.cli --platform sap --prompt-secret check
& .venv/Scripts/python.exe -m erp_connectors.cli --platform sap --prompt-secret browse databases
```

Use `python -m erp_connectors.cli --help` for metadata/preview/query commands.
`SAP_PASSWORD` is also accepted in the backend environment; prompting avoids a
persisted environment secret. Passwords are rejected in CLI JSON configuration.
For a fixed SAP service API process, set `ERP_CONNECTOR_MODE=sap` and
`ERP_CONNECTOR_CONFIG` to the reviewed CLI config, plus backend credentials/root
approval. The general profile endpoints work in the default `local-demo` mode.

## Testing without SAP

Run `python examples/sap_offline_demo.py` after package installation. It uses the
fictional XML/JSON fixtures under `demo/sap` through an in-memory HTTP adapter:
no cloud account, endpoint or billing is needed. It prints three financial rows
including a deliberately large decimal and a leading-zero document number.
These fixtures exercise the connector protocol and are **not SAP-hosted data**.
They are not a substitute for a live client acceptance test. The regular sample
panel remains the separate DuckDB demonstration.

`python -m unittest discover -s tests -p "test_sap*.py"` checks v2/v4 parsing,
requests URL preparation, paging, budgets, precision, input validation, credential
storage contracts, destination/link restrictions and API selection behavior.
The SAP connector has not been validated against a live SAP tenant in this
release. To accept it for a client, repeat steps 1–4 against that client's sandbox,
check denied business scope, certificate/token behavior if supplied by the host,
multi-page reads and reconciled totals before production integration.

Protocol references: [OData v2 JSON](https://www.odata.org/documentation/odata-version-2-0/json-format/),
[OData v4 JSON](https://docs.oasis-open.org/odata/odata-json-format/v4.01/odata-json-format-v4.01.html).
