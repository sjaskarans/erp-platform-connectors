# Snowflake connector: local setup and service identity

The second connector uses the official Snowflake Python driver with a dedicated
Snowflake `TYPE = SERVICE` user and RSA key-pair authentication. Customer account,
warehouse, role, user, and objects are configured in the product. No Snowflake
account, warehouse, user, grant, or data has been created remotely by this code.

## 1. Collect non-secret connection details

Get these from your Snowflake administrator:

- Account identifier, preferably `organization-account`, without `https://` or
  the `.snowflakecomputing.com` hostname suffix.
- An approved warehouse, for example `ERP_API_WH`.
- Dedicated reader role and service user names.
- Existing database, schema, and approved tables/views.
- Network policy or private connectivity requirements for your local machine.

In the shared object contract, `catalog` means Snowflake **database**. The UI
labels this field Database when Snowflake is selected. The connector quotes
identifiers; enter exact stored names. Objects created with unquoted Snowflake
SQL names are normally uppercase, such as `FINANCE_LINES` and `COMPANY_CODE`.

## 2. Generate a local encrypted RSA key pair

In your interactive PowerShell terminal, run:

```powershell
& .venv/Scripts/python.exe -m erp_connectors.snowflake_keys
```

The helper asks for a new passphrase twice, generates a 3072-bit RSA key, and
writes an encrypted PKCS8 private key and its public key to the ignored
`.local-connections/snowflake-keys` folder. It refuses to overwrite existing keys
and refuses an echoing terminal fallback. It makes no network calls.

Keep the private key and passphrase local. Register **only the public key** in
Snowflake. For an existing key pair, the connector accepts a valid RSA PEM private
key of at least 2048 bits, encrypted or unencrypted; encrypted PEM is recommended
for the original file's protection.

To get the public key body for the SQL below:

```powershell
$publicKeyBody = (Get-Content -LiteralPath .local-connections/snowflake-keys/rsa_key.pub | Where-Object { $_ -notmatch '^-----' }) -join ''
$publicKeyBody
```

This output is public-key material. Do not perform the equivalent operation on
the private key or put private-key contents into a query, source file, or chat.

[Snowflake key-pair configuration](https://docs.snowflake.com/en/user-guide/key-pair-auth)

## 3. Create the dedicated identity and role

An authorized user/role administrator creates the role and service user. Adapt
names and substitute only the public key body:

```sql
CREATE ROLE ERP_CONNECTOR_READER;

CREATE USER SVC_ERP_CONNECTOR
  TYPE = SERVICE
  DEFAULT_ROLE = ERP_CONNECTOR_READER
  DEFAULT_SECONDARY_ROLES = ()
  RSA_PUBLIC_KEY = '<public-key-body-without-PEM-headers>';

GRANT ROLE ERP_CONNECTOR_READER TO USER SVC_ERP_CONNECTOR;
```

Creating the default-role property does not grant that role; the explicit role
grant is required. Keep secondary roles disabled for this service identity and
review inherited/PUBLIC privileges. Do not grant the runtime identity SYSADMIN,
SECURITYADMIN, ACCOUNTADMIN, ownership, or ingestion privileges to make reads work.
Use a separate setup owner for tables, views, and grants.

[CREATE USER and role properties](https://docs.snowflake.com/en/sql-reference/sql/create-user)

## 4. Grant narrowly scoped read permissions

An administrator or object owner applies grants on the actual existing objects:

```sql
GRANT USAGE ON WAREHOUSE ERP_API_WH TO ROLE ERP_CONNECTOR_READER;
GRANT USAGE ON DATABASE ERP_ANALYTICS TO ROLE ERP_CONNECTOR_READER;
GRANT USAGE ON SCHEMA ERP_ANALYTICS.APPROVED TO ROLE ERP_CONNECTOR_READER;
GRANT SELECT ON VIEW ERP_ANALYTICS.APPROVED.FINANCE_LINES
  TO ROLE ERP_CONNECTOR_READER;
```

For a table, use `GRANT SELECT ON TABLE ...`. Grant individual approved objects
instead of all present and future objects. Preserve masking and row-access
policies. Review warehouse auto-resume, auto-suspend, and cost controls with its
owner; USAGE permits queries that can consume compute. Result row limits do not
limit scanned data or warehouse cost.

The connector uses the explicit reader role, JWT key-pair authentication, qmark
parameter binding, bounded fetches, a query tag, and a 120-second session statement
timeout. The operation deadline is not a hard wall-clock guarantee because network
calls and driver work can exceed it. It records Snowflake query IDs for audit
correlation and requests cancellation on known in-flight queries after failures.
Cancellation requests require confirmation of terminal status in Snowflake.

[Python connection parameters](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect),
[query execution and cancellation](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-example)

## 5. Configure through the connection screen

```powershell
$env:ERP_CONNECTOR_MODE = 'local-demo'
& .venv/Scripts/python.exe -m erp_connectors.demo --ui
```

Open `http://127.0.0.1:3000`, choose **Add connection**, and select **Snowflake**.
Enter the account identifier, warehouse, reader role, service username, PEM key,
and its passphrase. Add the approved database/schema/object names and explicit
preview columns. Select **Save & test connection** when your account is ready.

The backend encrypts the key/passphrase bundle with Windows DPAPI. The form is
cleared after saving, and profile responses exclude both fields. Leave both
fields empty when editing to retain stored credentials. To rotate credentials,
provide the replacement key and its passphrase together. Saving or changing a
profile does not grant Snowflake permissions, and changes reset verification.

The built-in sample-data button continues using the local DuckDB demo. It is
not a Snowflake sandbox and does not validate Snowflake authentication or policies.
This UI remains a single-operator local prototype; it has no hosted tenant sign-in.

## 6. Configure CLI queries if needed

The CLI supports registered query templates with positional `?` placeholders,
bound in the order of their declared parameters. Supported input types are
STRING, INT, DECIMAL, DATE, and BOOLEAN. Values remain strings/null at the API
boundary and are converted to driver-native values before binding.

Set non-secret environment values and the local encrypted-key file path:

```powershell
$env:SNOWFLAKE_ACCOUNT = '<organization-account>'
$env:SNOWFLAKE_USER = 'SVC_ERP_CONNECTOR'
$env:SNOWFLAKE_WAREHOUSE = 'ERP_API_WH'
$env:SNOWFLAKE_ROLE = 'ERP_CONNECTOR_READER'
$env:SNOWFLAKE_PRIVATE_KEY_FILE = (Resolve-Path .local-connections/snowflake-keys/rsa_key.p8).Path
```

Review `connector.snowflake.example.json` and use an appropriate copy in the
ignored `connector.local.json` file. Enter the private-key passphrase at the
hidden prompt:

```powershell
& .venv/Scripts/erp-connector.exe --platform snowflake --prompt-secret check
& .venv/Scripts/erp-connector.exe --platform snowflake --prompt-secret describe finance_lines
& .venv/Scripts/erp-connector.exe --platform snowflake --prompt-secret preview finance_lines --limit 5
& .venv/Scripts/erp-connector.exe --platform snowflake --prompt-secret query finance_by_currency --parameters '{"company_code":"1000"}'
```

For the root service's registered-query API, run it separately with
`ERP_CONNECTOR_MODE=snowflake`, an appropriate `ERP_CONNECTOR_CONFIG`, and approved
secret injection for `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`. Keep local-demo mode for
the UI's synthetic-data path; saved Snowflake profiles still open real Snowflake
connections independently of that demo mode. No arbitrary SQL browser endpoint
is exposed.

## 7. Optional synthetic Snowflake data

The committed `demo/snowflake` assets contain a fresh-schema setup script,
reader grants, six sample query templates, and known expected results. They use
the same nine fictional tables and 33 records as the Databricks/local fixtures.
Generate them for an existing database you own:

```powershell
& .venv/Scripts/python.exe -m erp_connectors.demo_assets --platform snowflake --catalog YOUR_DATABASE --schema erp_connector_demo_v1
```

Run `demo/snowflake/setup.sql` as the separate setup owner. The script creates a
new dedicated schema and inserts its synthetic data. Stop on the first error;
repeating INSERT statements duplicates data. Use a fresh demo schema and regenerate
all assets if an earlier setup exists. No database or warehouse is created.
Apply the generated reader grants only after the role exists. The expected
results are fixture-specific checks, not approved business metric definitions.

## 8. Verify live access and rotate keys

Before treating the connector as live-verified:

1. Check the returned effective user, role, and warehouse.
2. Inspect metadata and approved-column previews, including decimal precision,
   leading-zero identifiers, nulls, and date values.
3. Reconcile fixture queries with `expected.json` using decimal comparisons.
4. Confirm unrelated objects and writes are denied using effective-grant review
   and controlled disposable objects for active negative tests.
5. Verify masking/row-access policies, token/key revocation, query timeout,
   cancellation, and large-result failures in your account.

For this RSA_PUBLIC_KEY-based setup, register a new key in the unused
RSA_PUBLIC_KEY_2 slot, update the saved key and passphrase, verify the new
connection, and have the administrator remove the old public key. Snowflake
also supports named key-pair rotation; follow the appropriate procedure for your
identity configuration. Workload identity federation is a future authentication
option for an appropriate cloud runtime; this implementation uses local key pairs.

[Rotation procedures](https://docs.snowflake.com/en/user-guide/key-pair-auth)

Unit and local HTTP tests use generated disposable keys and simulated driver
responses. They do not establish actual Snowflake network access, permissions,
query behavior, or billing. No customer account has been tested yet.
