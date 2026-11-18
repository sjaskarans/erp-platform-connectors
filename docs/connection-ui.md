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
2. Name the profile and enter its exact Google Cloud Databricks workspace URL
   and SQL warehouse ID.
3. Enter the Databricks service principal's application/client ID and OAuth M2M
   secret. This is not a Google service-account JSON key.
4. Register approved objects: a short object label, catalog, schema, and table
   or view. Add explicit preview columns if you want row previews; leave them
   blank to disable previews.
5. Choose **Save for later** to persist an unverified profile, or **Save & test
   connection** to also check warehouse access and object metadata visibility.
6. After verification, use **Preview** to fetch up to five rows of approved
   columns from the first object with preview columns configured.

Saving does not create a workspace, warehouse, service principal, permission
grant, or business dataset. You need those resources to use a real connection.
The built-in **Explore sample data** path works without them.

The status is deliberately explicit: **Saved · not tested**, **Verified**, or
**Needs attention**. Verification checks a SQL connection and metadata for the
configured objects; it is not proof of every database policy or query scenario.

Use **Edit** to change a profile. An empty secret field retains the stored
secret; entering a new value replaces it. Editing resets verification status.
Multiple profiles retain separate workspaces, warehouses, identities, secrets,
and approved object lists. Removing a profile changes only local app state.

## Storage and API boundary

- Profile metadata is persisted in the ignored
  `.local-connections/profiles.sqlite3` file.
- OAuth secrets are encrypted with Windows DPAPI for the account running the
  backend. They cannot be decrypted simply by copying this database to another
  Windows account or computer under the normal DPAPI user-protection model.
- A typed secret is temporarily present in the browser form and its POST/PUT
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
