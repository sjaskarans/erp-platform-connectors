# Test Snowflake as a customer

Your Snowflake account, warehouse, and synthetic data already exist. Continue
here; do not repeat the data-loading script or generate a private key yourself.

1. Open [Connections](http://127.0.0.1:3000), click **Add connection**, and choose
   **Snowflake**. Name it, for example, `My ERP Snowflake`.
2. Enter your **account identifier** from Snowflake connection details, such as
   `organization-account`. Do not enter the Snowsight browser URL. If needed,
   run `SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME();`
   in your Snowflake worksheet to obtain it.
3. Enter your existing warehouse, e.g. `ERP_API_WH`. If you used a different
   warehouse when loading data, enter that exact name.
4. Choose fresh dedicated names for the reader role and service user, e.g.
   `ERP_CONNECTOR_READER` and `SVC_ERP_CONNECTOR`. These identify the application,
   not your personal Snowflake login. If you have already created these names,
   choose unused names for this new profile.
5. The default **Discover data from an administrator-approved schema** option
   needs just database `ERP_DEMO` and schema `ERP_CONNECTOR_DEMO_V1`. Its generated
   SQL grants access to all current tables/views in that schema, with no future
   grants. Your administrator must approve that scope. After connecting, browse
   the schema and select the tables/columns you want the app to use.

   If your administrator requires individual-table source permissions, turn
   schema discovery off and add an approved object using the names you loaded:

   | Field | Example |
   | --- | --- |
   | Object label | `finance_lines` |
   | Database | `ERP_DEMO` |
   | Schema | `ERP_CONNECTOR_DEMO_V1` |
   | Table or view | `FINANCE_LINES` |
   | Object type | Table |
   | Preview columns | `COMPANY_CODE, AMOUNT, CURRENCY` |

   Use the actual names shown in your Snowflake account if they differ. Names
   are case sensitive in the connector because identifiers are quoted exactly.
   Add more objects now, or begin with this one to test connectivity.
6. Click **Continue to setup SQL**. The backend generates an RSA 3072-bit keypair,
   saves the private key encrypted for your Windows user, and displays only
   the public key inside a provisioning script. No key file or terminal command
   is required from you.
7. Click **Copy SQL**. Open a new Snowflake SQL worksheet and select
   **ACCOUNTADMIN** for your personal trial. In an organization, give the script
   to the administrator authorized to create users/roles and grant source access.
   Review and run the statements once, in order; stop on any error. The user and
   role must be new. Do not replace an existing user's key to make it work.
8. Return to the app and click **I ran the SQL · Test connection**. A successful
   test verifies authentication, the selected warehouse, object visibility,
   and a one-row SELECT for each object with preview columns configured.
9. For schema discovery, the successful login opens **Browse data**. Navigate
   databases → schemas → tables/views; select objects and use **Choose columns**
   for the fields to preview. Save selection, then test access on the card.
10. Click **Preview** on your connection card. This reads up to five rows of the
   first configured object with preview columns from your actual Snowflake
   account. **Explore sample data** is a separate local demo.

The script creates a TYPE=SERVICE identity, a dedicated reader role, grants that
role to the service user and SYSADMIN, and grants warehouse/database/schema
USAGE plus SELECT on the approved current schema contents or listed tables/views. It does not create, replace, or
reload business tables. Warehouse queries may consume Snowflake credits.

You can finish later and reopen **Setup SQL** without changing the key. After
editing an existing profile, ask your administrator to apply only necessary
grant changes rather than rerunning CREATE statements. Removing a local profile
does not revoke Snowflake access; an administrator must revoke/drop the dedicated
identity and role separately when retiring it.

The setup panel keeps test progress and the result visible beside the test
button, even when its SQL area is scrolled. A key-pair login rejection requires
checking the service identity: run the displayed `DESCRIBE USER` command as an
administrator and compare `RSA_PUBLIC_KEY_FP` (or `RSA_PUBLIC_KEY_2_FP`) with the
app's public fingerprint. Also check `LOGIN_NAME`, account identifier, and the
local clock. Never assume a generic JWT rejection proves a key mismatch.

If the user is missing but the new reader role already exists from a partial
setup, run the original generated CREATE USER statement (with its original public
key) and the remaining GRANT statements. Skip CREATE ROLE for the role you
already created. If the user already exists, stop and inspect its ownership and
registered fingerprint rather than replacing its key blindly.

If testing fails, confirm account identifier and exact names, all SQL statements
succeeded, warehouse availability, approved columns, and applicable Snowflake
network/authentication policies. Do not paste private keys or passwords into chat.

This is the customer onboarding experience running locally. Windows DPAPI
protects local credentials; hosted multi-customer deployment still requires
sign-in, tenant isolation, managed secret storage, and credential rotation.

References: [Snowflake service users](https://docs.snowflake.com/en/sql-reference/sql/create-user)
and [key-pair authentication](https://docs.snowflake.com/en/user-guide/key-pair-auth).
