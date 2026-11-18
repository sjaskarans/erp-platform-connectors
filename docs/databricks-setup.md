# Google Cloud Databricks: identity, permissions, and first read

Target platform: **Databricks on Google Cloud**, as confirmed for this project.
The Python connector will run on the user's **local Windows machine** initially.
Start with [the local walkthrough](local-databricks.md) and Databricks OAuth M2M.
The federation and Google Cloud runtime instructions below are future deployment
options. No Google Cloud resources are needed for this initial local setup.

Keep these identities separate:

| Identity | Purpose | Permission system |
| --- | --- | --- |
| Databricks service principal | Execute SQL and inspect approved metadata | Databricks warehouse permissions and Unity Catalog |
| Google Cloud service account, if hosting on GCP | Identify the Python runtime; access its specific secret or supply federated identity | Google Cloud IAM |

Creating a Google service account does not automatically grant Databricks data
access. For the initial connection, use a Databricks service principal with OAuth
M2M. For supported production runtimes, prefer federation into that principal.

## 1. Collect non-secret connection details

Ask the workspace/data owner for:

- Exact Google Cloud Databricks HTTPS workspace URL from the workspace owner,
  commonly `https://<workspace-id>.<deployment-id>.gcp.databricks.com`.
  Do not use the account-console URL for SQL requests.
- SQL warehouse ID. The Statement Execution API uses an ID, not the HTTP path.
- Unity Catalog catalog, schema, approved tables/views, and approved preview columns.
- Workload hosting environment and preferred OAuth method.
- Network route, workspace IP restrictions/private connectivity, and an owner
  who can provision the service principal and object grants.

The implementation uses the workspace SQL Statement Execution API. Confirm that
the target deployment and warehouse support it. Legacy Hive Metastore discovery
and direct SAP connections are not implemented in this first connector.

## 2. Create the service principal

Have an account/workspace identity administrator:

1. Open `https://accounts.gcp.databricks.com` and its user/identity management area.
2. Create a dedicated Databricks service principal, such as
   `sp-erp-connector-dev`. Use separate principals for test and production.
3. Record both its application/client ID and its Databricks numeric ID. They
   serve different purposes: SQL grants use the application ID; account APIs
   for federation policies use the principal's numeric ID.
4. Assign the principal to the intended workspace and grant the SQL entitlement
   required by that workspace's access model.
5. Restrict who can manage or use the principal. Do not make it a workspace or
   account administrator.

Use a Databricks-managed principal for this path. A Google service-account email,
JSON private key, or Google OAuth client secret is not a replacement for this
principal's Databricks client ID and Databricks OAuth secret.

Official references:
[service principal OAuth on Google Cloud](https://docs.databricks.com/gcp/en/dev-tools/auth/oauth-m2m).

## 3. Grant compute access

1. Open **SQL Warehouses** and select a warehouse dedicated to, or approved for,
   this application.
2. Open its permissions and grant the principal **CAN USE**.
3. Record the warehouse ID from its connection details.
4. Configure auto-stop, permitted size/scaling, and monitoring with its owner.
   A row limit restricts returned results, not warehouse scan cost.

## 4. Grant approved data access

Run the following as an authorized data owner or administrator. Substitute the
real catalog, schema, views, and principal application/client ID:

```sql
GRANT USE CATALOG ON CATALOG erp_analytics
TO `<principal-application-id>`;

GRANT USE SCHEMA ON SCHEMA erp_analytics.approved
TO `<principal-application-id>`;

GRANT SELECT ON TABLE erp_analytics.approved.finance_lines
TO `<principal-application-id>`;
```

Grant specific objects rather than schema-wide SELECT by default. In Databricks,
the TABLE securable syntax is used for the SELECT grant on a view as well.
Prefer approved views for SAP/ERP fields that require masking, restricted legal
entities, or curated joins. The connector does not need CREATE, MODIFY, MANAGE,
ownership, storage credentials, or access to raw object storage.

Review effective access through groups and inherited privileges, including
catalogs that have broad defaults. The application should query the approved
objects without bypassing their row filters or masking policies.

Discovery queries `<catalog>.information_schema.columns` for each configured
object and uses the principal's existing metadata visibility. Do not grant
account-wide metadata or query-history access for this first milestone.

[Unity Catalog privilege reference](https://docs.databricks.com/gcp/en/data-governance/unity-catalog/access-control/privileges-reference)

## 5. Choose authentication

### Option A: workload identity federation, preferred for supported runtimes

1. Establish a workload identity with your hosting provider. Ensure the runtime
   produces and refreshes a signed OIDC identity token for this specific workload.
2. As a Databricks account administrator, add a federation policy to the service
   principal with the exact issuer, subject, and audience claims.
3. Use a narrowly scoped subject for this workload and environment. Do not trust
   all identities from an issuer.
4. Arrange a renewable token file mounted into the Python service, then set:

```powershell
$env:DATABRICKS_HOST = 'https://<workspace-host>'
$env:DATABRICKS_CLIENT_ID = '<principal-application-id>'
$env:DATABRICKS_AUTH_TYPE = 'file-oidc'
$env:DATABRICKS_OIDC_TOKEN_FILEPATH = '<absolute-path-to-renewed-identity-token>'
$env:DATABRICKS_SQL_WAREHOUSE_ID = '<warehouse-id>'
```

The token file contains an external workload identity token, not a Databricks PAT
or a cached Databricks access token. The workload identity system must refresh it;
setting a file path does not create or refresh the external identity.

For Google-hosted workloads, use a dedicated Google service account and a
Google-signed ID token for that identity. Match its actual issuer, numeric
service-account subject, and audience in the Databricks federation policy. A
Google API access token is not an OIDC ID token. The audience must match the
Databricks federation policy, not a Cloud Run service URL chosen for a different
service-to-service call.

On supported runtimes, such as Cloud Run or Compute Engine, a token producer can
use the metadata identity endpoint. On GKE the identity configuration depends on
the cluster and workload identity setup. This repository does not yet implement
a renewable Google identity-token producer; the connector supports the SDK's
renewed token-file input. Finish the runtime-specific token producer and renewal
verification before selecting `file-oidc`.

Do not confuse this flow with Databricks' separate legacy Google ID
authentication mode, which has its own identity setup and limitations. This
connector currently accepts service-principal OAuth modes only.

[Federation policy setup](https://docs.databricks.com/gcp/en/dev-tools/auth/oauth-federation-policy),
[token exchange configuration](https://docs.databricks.com/gcp/en/dev-tools/auth/oauth-federation-exchange),
[Google ID-token acquisition](https://docs.cloud.google.com/docs/authentication/get-id-token).

### Option B: Databricks OAuth M2M for initial setup or unsupported federation

1. Open the service principal's details and **Secrets** section.
2. Generate an **OAuth secret**, with a short operationally practical lifetime.
3. Copy it once into the deployment's managed secret store.
4. Inject it into the Python process and configure:

```powershell
$env:DATABRICKS_HOST = 'https://<workspace-host>'
$env:DATABRICKS_CLIENT_ID = '<principal-application-id>'
$env:DATABRICKS_AUTH_TYPE = 'oauth-m2m'
$env:DATABRICKS_CLIENT_SECRET = '<injected-from-secret-store>'
$env:DATABRICKS_SQL_WAREHOUSE_ID = '<warehouse-id>'
```

The placeholder above illustrates the environment contract. Avoid entering real
secrets into saved scripts or terminal history. Inject them with your secret
manager or an approved interactive method. The SDK obtains and refreshes the
short-lived Databricks access tokens. Rotate the OAuth secret before expiry and
revoke the old secret after verifying the new deployment.

The CLI explicitly permits only service-principal OAuth modes and will not
silently fall back to a personal token or interactive developer identity.

### Google Cloud runtime identity and Secret Manager, for Option B

These steps apply only if the Python connector will run on Google Cloud. They
are administrative instructions, not commands the connector runs. For an initial
local connection, the operator can use the approved local secret-injection path
without provisioning a cloud runtime yet.

1. In **IAM & Admin → Service Accounts**, create `erp-databricks-dev` in the
   application's project. Do not assign broad project roles in the creation
   wizard and do not generate a JSON private key.
2. In **Secret Manager**, create a secret named `databricks-erp-dev-oauth-secret`
   using the organization-approved location/replication policy. Add the OAuth
   secret value as a version through the approved secure process.
3. On that individual secret's **Permissions** tab, grant the runtime service
   account **Secret Manager Secret Accessor**. It does not need Secret Manager
   Admin or project-wide secret access. The equivalent binding is:

```powershell
gcloud secrets add-iam-policy-binding databricks-erp-dev-oauth-secret --project=<project-id> --member="serviceAccount:erp-databricks-dev@<project-id>.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
```

4. Attach this service account to the eventual runtime. Its deployment identity
   needs permission to attach that account; do not give that deployment privilege
   to the connector itself solely to run queries.
5. Configure the runtime's supported Secret Manager integration to inject a
   selected secret version into `DATABRICKS_CLIENT_SECRET`. Set the workspace
   URL, Databricks client ID, warehouse ID, and `oauth-m2m` auth mode separately.
   This starter does not fetch Secret Manager secrets directly.
6. Recreate the setup with distinct identities and secrets for test/production.
   Validate rotation and revoke the replaced OAuth secret.

The Google runtime service account needs no BigQuery, Storage, Owner, Editor,
or IAM-admin role just to submit Databricks SQL through this path. Grant any
additional operational access separately and narrowly. The Databricks principal
still needs the warehouse and Unity Catalog grants from steps 3–4 above.

[Create Google service accounts](https://docs.cloud.google.com/iam/docs/service-accounts-create),
[grant access to an individual secret](https://docs.cloud.google.com/secret-manager/docs/manage-access-to-secrets).

## 6. Configure existing ERP objects

Copy `connector.example.json` to `connector.local.json` and replace all fictional
object/column names. Register only objects approved for this application.

Set `preview_columns` to an explicit list of non-sensitive columns, or leave it
empty to disable previews. Metadata discovery does not fetch sample rows.

The example `finance_by_currency` query groups currencies separately. Its SUM is
only a connectivity example, not a revenue or other business-metric definition.
Have the data owner review signs, currency, grain, and access restrictions before
using any figures. Treat the query registry as reviewed deployment configuration.

## 7. Perform the first read

From the workspace root after installation:

```powershell
& .venv/Scripts/erp-connector.exe check
& .venv/Scripts/erp-connector.exe describe finance_lines
& .venv/Scripts/erp-connector.exe preview finance_lines --limit 5
& .venv/Scripts/erp-connector.exe query finance_by_currency --parameters '{"company_code":"1000"}'
```

Inspect the typed columns, full result, and statement ID. Locate the execution
in Databricks query history as an authorized operator. No separate connector
query-history permission is required merely to record the execution ID.

## 8. Complete live acceptance checks

Use a development environment and the real runtime principal:

1. `check` succeeds and the warehouse records the intended service principal.
2. Discovery returns the expected columns and comments for each approved object.
3. An approved preview and query succeed; compare their output with a trusted
   query run by the data owner.
4. Confirm unrelated data access and mutation of existing business data are denied.
   Have the administrator validate effective privileges and use designated
   disposable objects for any active write-denial test.
5. Verify the applicable legal-entity restrictions and masking rules using
   controlled test records.
6. Verify credential renewal/rotation and a permission revocation.
7. Verify a slow query times out and reconcile the cancellation in Databricks.
8. Verify an oversized query fails with `RESULT_LIMIT_EXCEEDED`, not partial data.
9. Verify logs contain no rows, SQL text, secret values, or query parameter values.

Record the workspace, identity, object mapping, query registry version, execution
IDs, and expected/actual outcomes in your deployment acceptance record. These
checks require customer access and have not been performed by the offline tests.

## 9. Handoff to the next platform

Once live acceptance passes, implement Snowflake against the same connector
contract and result shape, then Redshift. Plan direct SAP separately against its
actual edition and supported APIs. Existing SAP-originated warehouse data does
not require a separate SAP extraction identity just to query those copies.

## Deployment boundary

This starter is for one operator-managed connection. Before exposing a service,
add authenticated caller authorization, per-customer identity/configuration
isolation, operation ownership, durable execution state, bounded concurrency,
secret-manager integration, and an audit sink with a retention policy. Do not
expose `cancel(statement_id)` to arbitrary clients: associate each operation with
its authorized owner first.

API behavior reference:
[SQL Statement Execution](https://docs.databricks.com/gcp/en/dev-tools/sql-execution-tutorial),
[official Python SDK](https://databricks-sdk-py.readthedocs.io/en/latest/workspace/sql/statement_execution.html).
