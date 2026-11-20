# Customer-configurable connection screen

The Next.js screen is the local onboarding interface. Workspace URLs, warehouse
IDs, service identities, and approved objects are entered by the operator in the
product; they are not hard-coded into the connector implementation.

## Open it

From the project folder in a normal interactive PowerShell terminal:

```powershell
& .venv/Scripts/python.exe -m erp_connectors.demo --ui
```

Open [Connections](http://127.0.0.1:3000). The launcher starts the Next.js app,
the Python API gateway, and the Python connector service. Press Ctrl+C in that
terminal to stop them. If these services are already running from an older
session, stop that session first. The browser interface does not need the local
API token; Next.js uses it on the server, without exposing it to browser code.

Dependencies are already installed in this workspace. On a fresh checkout, run
the Python installation from README and `npm install` in `frontend` first.

## Configure a connection

1. Click **Add connection**.
2. Select **Databricks** or **Snowflake** and name the profile. Databricks uses
   its workspace URL and warehouse ID; Snowflake uses its account identifier,
   warehouse, and reader role.
3. For Databricks, enter the service principal client ID and OAuth secret.
   For Snowflake, choose a **new** dedicated service username and reader role.
   The app generates the RSA private key on the backend and encrypts it with Windows DPAPI.
4. For Snowflake, use **Discover data from an administrator-approved schema**:
   enter the database/schema once. Its setup SQL explicitly grants SELECT on all
   current tables/views in that schema, with no future grants. After login, use
   **Browse data** to select objects and columns. Turn schema discovery off when
   an administrator requires individual-object source grants.
   For Databricks or the individual-object Snowflake option, register approved objects: a short label, database/catalog, schema, and table
   or view. Select the Snowflake object type. Enter exact stored names (usually
   uppercase for Snowflake), and explicit preview columns to enable row previews.
5. For Snowflake, choose **Continue to setup SQL**, copy the generated SQL, and
   run it once in your Snowflake worksheet using an administrator authorized to
   create identities and grant access. Your personal trial can use ACCOUNTADMIN.
   Stop if the dedicated user or role already exists; do not overwrite it.
6. Click **I ran the SQL · Test connection**, then **Preview** on the saved card.
   Databricks continues to offer **Save for later** and **Save & test connection**.

For Snowflake instructions starting with your already loaded synthetic ERP data,
see [Customer Snowflake testing](snowflake-customer-testing.md). Do not reload
or recreate those tables. The setup SQL grants parent/warehouse USAGE and SELECT
on only the chosen objects. Administrators should inspect it before execution.

**Setup SQL** on a saved Snowflake card reopens the same public key and SQL.
It never generates another key. The script is for initial provisioning, not
blind reruns: after a profile edit, apply only required grants to the existing
identity. Editing retains its key and resets verification. For a separate
identity/key, create a new profile with fresh service user and role names.

Verification checks the SQL connection and object metadata; objects with preview
columns also get a one-row SELECT check. Objects without preview columns have
metadata visibility checked only. Broader queries and policies need separate testing.

Use **Edit** to change profile details. An empty Databricks OAuth secret retains
the stored secret. The Snowflake UI never asks for a private key. Developer API
and CLI key import remain available for operator-managed integrations.

Multiple profiles retain separate workspaces, warehouses, identities, secrets,
and approved object lists. Removing a profile changes only local app state.

## Storage and API boundary

- Profile metadata is persisted in the ignored
  `.local-connections/profiles.sqlite3` file.
- OAuth secrets and Snowflake key/passphrase bundles are encrypted with Windows DPAPI for the account running the
  backend. They cannot be decrypted simply by copying this database to another
  Windows account or computer under the normal DPAPI user-protection model.
- A typed Databricks secret is temporarily present in the browser form and its POST/PUT
  request, then cleared after a successful save or closing the form. It is not
  put in browser storage or returned in profile API responses. The local service
  decrypts it only to open that profile's connection.
- This is a loopback-only, single-operator prototype. Loopback is not user
  authentication: other clients that can reach this computer's local UI may use
  it while the server is running. Do not expose it on a shared/public server.
- Hosted customer onboarding still requires user sign-in, server-derived tenant
  ownership on every connection, managed secret storage, TLS, audit controls,
  rate/concurrency limits, and protection against cross-customer access. The
  current profile IDs are not a substitute for tenant authorization.

Connection API routes live under `/v1/connections`: list/create, update/delete
by profile ID, `POST /{id}/test`, and
`GET /{id}/objects/{object}/preview`. Validation errors omit submitted values so
that malformed secret fields cannot be echoed back in error responses.

The CLI environment-variable path remains available for developer automation.
The user-facing screen supplies configuration directly to a new connector
instance for the selected profile, rather than changing global process settings.

## Browse data reference flow

On a Snowflake card, **Browse data** opens databases → schemas → tables/views →
columns. Metadata is read under the connection's service role. Search uses an
exact-case prefix and each page contains up to 100 items. Select listed objects
or columns in a batch; the reference profile supports 30 objects and 30 preview
columns each. Columns are selected explicitly, so checking a table alone enables
metadata but does not enable row previews.

Review the selection, save it, then test access. Saving validates source column
names and uses the profile version to avoid overwriting newer configuration.
The identity and key remain unchanged. Preview includes an approved-object
selector for switching between selected tables. New tables are not selected
automatically, and deselecting an object does not revoke Snowflake grants.

The screen is a reference integration client, not a separate client-facing product.
See [team integration](team-integration.md) for SDK and API usage.

## Amazon Redshift reference flow

Choose **Amazon Redshift**, its region, database, deployment type, and workgroup
name or cluster identifier. Enter the dedicated backend AWS profile name, or
leave it blank for the host's workload provider chain. The profile stores no AWS
keys or database passwords. Configure IAM and database read grants using
[Redshift setup](redshift-setup.md), save/test, then use the same Browse data →
selection → preview flow. Metadata browsing is scoped to the configured database;
add another profile for a different database. This connector is offline-tested;
a real Redshift smoke test is pending.
