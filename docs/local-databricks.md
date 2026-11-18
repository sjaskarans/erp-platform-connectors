# Local machine → Google Cloud Databricks

Use the existing Windows Python environment in this project and a dedicated
Databricks service principal. For this initial local OAuth M2M path, you do not
need a Google Cloud service-account key, a Cloud Run deployment, or GCP Secret
Manager. A Google login to the workspace does not by itself authorize the Python
service principal.

## 1. Have the Databricks administrator provision access

Follow steps 2–4 in [the setup guide](databricks-setup.md):

1. Create a Databricks service principal and assign it to your GCP workspace.
2. Enable the workspace access/SQL entitlement required by that workspace.
3. Grant **CAN USE** on the intended SQL warehouse.
4. Grant **USE CATALOG**, **USE SCHEMA**, and **SELECT** on the approved objects.
5. Generate a Databricks OAuth secret for this principal. Transfer it using your
   approved secret-sharing process. Do not paste it into chat.

Keep separate development and production identities. The detailed guide includes
the SQL grants and administrator steps. Credentials do not bypass workspace
network policies: connect through the organization's approved VPN or network
route when private access or IP restrictions require it.

## 2. Set only the non-secret connection details

Open an interactive PowerShell terminal in this project folder. The installed
`.venv` already contains the connector and official Databricks SDK.

```powershell
$env:DATABRICKS_HOST = 'https://<actual-workspace-host>.gcp.databricks.com'
$env:DATABRICKS_CLIENT_ID = '<service-principal-application-id>'
$env:DATABRICKS_SQL_WAREHOUSE_ID = '<sql-warehouse-id>'
$env:DATABRICKS_AUTH_TYPE = 'oauth-m2m'
```

Copy the exact workspace URL rather than deriving it from the example. These
variables last for this PowerShell session. No OAuth secret needs to be set in
the environment when using the hidden prompt below.

## 3. Configure approved ERP objects

If the local configuration does not exist yet, copy the example without
overwriting an existing one:

```powershell
if (-not (Test-Path -LiteralPath connector.local.json)) {
    Copy-Item -LiteralPath connector.example.json -Destination connector.local.json
}
```

Edit `connector.local.json` with your approved catalog, schema, table/view,
preview columns, and reviewed query templates. The example objects are fictional.
Never put credentials in this file. Leave `preview_columns` empty to disable
sample-row reads until approved. The file is excluded from Git.

## 4. Check authentication and warehouse access

Run this directly in an interactive terminal:

```powershell
& .venv/Scripts/erp-connector.exe --prompt-secret check
```

Enter the OAuth secret at the hidden prompt. The connector passes it directly
to the SDK; it does not write it to disk or put it in the process environment.
It remains in process memory for the command's lifetime; this is not a promise
of memory erasure. The prompt refuses Python's echoing fallback when a secure
terminal is unavailable. Do not pipe credentials into the command.

Expected result: a complete result containing `connected = "1"`, column types,
and a Databricks statement ID. This checks compute access; object access is
checked separately below.

## 5. Fetch metadata, then a small preview

```powershell
& .venv/Scripts/erp-connector.exe --prompt-secret describe finance_lines
& .venv/Scripts/erp-connector.exe --prompt-secret preview finance_lines --limit 5
```

Substitute your configured object ID. Each process asks for the secret again.
Metadata includes column names, types, nullability, order, and comments. Preview
returns the approved columns only. Review the output with your data owner.

## 6. Execute a registered query

```powershell
& .venv/Scripts/erp-connector.exe --prompt-secret query finance_by_currency --parameters '{"company_code":"1000"}'
```

Use a company code you are authorized to read and a template reviewed against
your actual schema. This example SUM is a connection demonstration, not a
governed business metric. Currency, signs, reversals, and grain will be defined
in the semantic layer.

Query results appear in your terminal; treat them as company data. Audit events
go to stderr. The result's `fetched_at` is retrieval time, not ingestion freshness.

## 7. Record the first successful connection

Complete the live acceptance checks in the detailed guide. In particular,
confirm the runtime principal in Databricks, reconcile an approved query with
the data owner, and verify effective access excludes unrelated objects and
mutation privileges. No live connection has been established by creating these
local files or running offline tests.

If a command fails, share only the error code and statement ID plus non-secret
connection details. An authorized administrator can investigate that execution.
A `CONFIGURATION_OR_AUTHENTICATION_ERROR` before any statement ID generally
requires checking configuration, credentials, or network access. Do not send the
OAuth secret or full SDK debug logs.

Official authentication reference:
[Databricks OAuth M2M on Google Cloud](https://docs.databricks.com/gcp/en/dev-tools/auth/oauth-m2m).
