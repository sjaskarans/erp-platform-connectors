# Redshift connector: IAM setup and team integration

The third connector uses the official AWS SDK and Redshift Data API. It supports
Redshift Serverless and provisioned clusters with IAM-derived database identities.
It is a Python connector module with a reference API/UI, intended for integration
into the team's larger application. No AWS resources have been created or live
Redshift connections verified for this delivery.

## 1. Choose or create a test environment

If the client already has Redshift, use their existing workgroup/cluster and
approved database. For a new test environment, an AWS administrator can create a
Serverless namespace/workgroup in the AWS console, choose a region and database,
and set usage/cost controls before testing. Provisioned clusters must support the
Data API. Both provisioned compute and Serverless activity can incur charges.
The connector never creates infrastructure or enables billing.

Record the region, database, and either workgroup name or cluster identifier.
Record the target ARN for IAM policy configuration. A Serverless workgroup ARN
uses its **workgroup ID**, while the connector takes its **workgroup name**.
These are different values. Use one connection profile per database.
The desktop talks to the regional AWS Data API over HTTPS; making the database
TCP endpoint public is not required solely for this connector.

## 2. Create the application's IAM role

Create a dedicated role, for example `ERPConnectorRole`, for this client's
connector. For Windows testing, use federated sign-in through AWS IAM Identity
Center and assume this role. Do not create long-lived access keys or paste keys
into the connector UI.

The administrator configures the role's trust policy to permit the specific
existing sign-in role to assume it. Replace both placeholders below with the
actual trusted account and full role path/name:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::<ACCOUNT_ID>:role/<EXISTING_SIGN_IN_ROLE>"},
    "Action": "sts:AssumeRole"
  }]
}
```

The existing sign-in role also needs `sts:AssumeRole` on the exact
`ERPConnectorRole` ARN. Administrators should apply their organization's MFA,
session-duration, and cross-account external-ID requirements where applicable.
For a deployed workload, use the application's workload role/identity provider.
The connector accepts an injected AWS client or the standard AWS provider chain.

This role authorizes the application's AWS API calls. Attaching an S3/COPY role
to a Redshift warehouse does not configure the Windows application's identity.

## 3. Attach the scoped permission policy

Use the appropriate template and replace every `<PLACEHOLDER>`:

- [Serverless policy](redshift-iam-serverless.template.json): Data API execution
  on one workgroup, `redshift-serverless:GetCredentials` for that workgroup, and
  result/status/cancel operations for the calling IAM session's own statements.
- [Provisioned policy](redshift-iam-provisioned.template.json): Data API execution
  on one cluster and `redshift:GetClusterCredentialsWithIAM` on the exact
  cluster/database `dbname` ARN, plus the same statement-owner restrictions.

Do not substitute the cluster ARN for the provisioned credentials action's
`dbname` ARN. Result/status/cancel actions use `Resource: "*"` because these
operations do not expose a statement resource ARN; their owner condition is the
restriction. No Secrets Manager, S3, IAM-administration, infrastructure creation,
or Data API batch/list-statement permissions are needed by this implementation.
Metadata browsing runs bounded SELECT queries, so separate Data API metadata-list
actions are not required.
The AWS administrator must also ensure any required Data API service-linked role
has been initialized for the account; the connector role does not create it.

IAM permission to execute SQL does not make a database user read-only. The next
steps establish the database grants. Keep the role's other attached/inherited
policies narrow as well.

## 4. Configure the Windows AWS profile

Install AWS CLI v2 from AWS and configure a federated source profile:

```powershell
aws configure sso --profile erp-source
aws sso login --profile erp-source
```

Add the dedicated assume-role profile to the Windows user's `.aws/config`:

```ini
[profile erp-connector]
role_arn = arn:aws:iam::<ACCOUNT_ID>:role/ERPConnectorRole
source_profile = erp-source
role_session_name = erp-connector-desktop
region = us-east-1
```

Verify the resulting identity locally:

```powershell
aws sts get-caller-identity --profile erp-connector
```

The ARN should identify the dedicated assumed role. Use the same Windows identity
to run the backend. IAM Identity Center tokens and temporary credentials remain
in AWS's provider/cache, outside the connector profile database. Sign in again
when the SSO session expires. The SDK refreshes supported temporary credentials;
it cannot silently renew an expired interactive SSO session.

## 5. Connect and identify the database user

Copy `connector.redshift.example.json` to the ignored `connector.local.json`.
Set region, database, target name, and `aws_profile`. For provisioned Redshift,
set `deployment` to `provisioned`, remove `workgroup_name`, and provide
`cluster_identifier`. Do not add `DbUser`, a database password, a secret ARN, or
AWS access keys; this connector intentionally uses IAM-derived authentication.

```powershell
& .venv/Scripts/python.exe -m erp_connectors.cli --platform redshift --config connector.local.json check
```

The check returns `current_user` and `current_database()` through the Data API.
Use the **exact returned database username** for grants, commonly
`IAMR:ERPConnectorRole` for an assumed IAM role. The initial IAM connection may
create its federated database user. If authentication is blocked before that,
the administrator must resolve it before granting application data access.
The AWS role is not automatically granted database table access.

## 6. Load synthetic ERP data, or use existing approved data

For a new test account, open an administrator SQL worksheet connected to the
same database and run `demo/redshift/setup.sql` in order, stopping on errors.
It creates a fresh `erp_connector_demo_v1` schema, 9 tables, and 33 fictional rows.
It never drops or replaces existing objects. Do not rerun INSERT statements into
an already loaded schema. The script does not create a workgroup or database.

The test assets can be regenerated locally without AWS calls:

```powershell
& .venv/Scripts/python.exe -m erp_connectors.demo_assets --platform redshift --catalog dev --schema erp_connector_demo_v1
```

`expected.json` defines expected finance totals and row counts.
`connector.demo.json` includes the target placeholders, approved objects, typed
named query templates, and limits. Set its target/profile fields before using it
with the CLI. The dates are fixed fixture dates, not a live report of today.

For real client data, skip synthetic loading and choose an approved existing
schema or curated reporting views.

## 7. Grant read-only database access

As an administrator in the configured database, create a dedicated database role
if absent, then grant only the needed schema and objects. Replace the IAM-derived
username with the exact result of step 5:

```sql
CREATE ROLE erp_connector_reader;
GRANT USAGE ON SCHEMA erp_connector_demo_v1 TO ROLE erp_connector_reader;
GRANT SELECT ON TABLE erp_connector_demo_v1.finance_lines TO ROLE erp_connector_reader;
GRANT ROLE erp_connector_reader TO "IAMR:ERPConnectorRole";
```

For the full synthetic fixture, use `demo/redshift/grants.template.sql`, which
lists all nine tables. For an administrator-approved reporting schema, an
alternative is `GRANT SELECT ON ALL TABLES IN SCHEMA <schema> TO ROLE
erp_connector_reader`; Redshift applies this to current tables and views.
No future grants are included. Inspect an already existing role before reusing
it rather than dropping/replacing identities.

Verify the user has no unneeded write, create, owner, superuser, group, or
inherited PUBLIC privileges on business objects. Changing PUBLIC grants affects
other users and is intentionally not automated by these scripts.

## 8. Use the reference screen or SDK

In the local connection screen, choose **Amazon Redshift**, enter target details
and the AWS profile name, then save/test. A connection without selected objects
is `connected`; **Browse data** then exposes the configured database → schemas →
tables/views → columns. Select objects/columns, save, test, and preview. The app
never types or stores AWS credentials, creates IAM roles, or runs grant SQL.

The SDK can be embedded directly into the larger application:

```python
import boto3
from botocore.config import Config
from erp_connectors import DataObject, RedshiftConnector

client = boto3.Session(profile_name="erp-connector", region_name="us-east-1").client(
    "redshift-data", config=Config(connect_timeout=5, read_timeout=15,
                                  retries={"mode": "standard", "total_max_attempts": 2}))
connector = RedshiftConnector(client, "dev", {}, workgroup_name="erp-analytics")
try:
    metadata = connector.browse("objects", database="dev", schema_name="erp_connector_demo_v1")
finally:
    connector.close()
```

For provisioned Redshift, pass `cluster_identifier="..."` instead of
`workgroup_name`. The target is validated: exactly one is required. Pass the
application's selected `DataObject` registry to enable previews, and its reviewed
`QueryTemplate` registry for analytical queries. Parameter placeholders use
`:name`, with values passed separately through `Parameters`; use explicit SQL
casts for typed parameters where needed. Empty strings and SQL NULL cannot be
bound by this Data API path; define explicit approved SQL variants for those
cases rather than passing the text `NULL`.

API routes are shared with Snowflake for create/update/test/browse/selection/
preview. Redshift profiles store only non-secret settings. The key-generation
`setup` endpoint belongs to Snowflake; use this guide for Redshift IAM setup.

## Validation and current boundaries

- Offline tests exercise the actual boto3/botocore API model with Stubber for
  both target types. Other tests cover named binding, pagination/completeness,
  exact decimals, nulls, byte/row/page limits, timeouts/cancel, sanitization,
  provider profiles, and the reference API/UI. A real AWS smoke test is pending.
- The configured database is the browsing and object-access boundary. Add a
  profile for another database. Discovery currently targets local base tables
  and standard views through `information_schema`; external/Spectrum objects,
  datashares, materialized/late-binding view discovery need separate support.
- Metadata pages contain at most 100 items; search uses an exact-case prefix.
  The reference profile supports 30 objects and 30 preview columns each. Source
  privileges are enforced by Redshift independently of the application selection.
- Results are completely paginated within the configured row/byte/time/page
  budgets, and fail rather than returning misleading partial totals. Decimal
  strings and BIGINT values are preserved; decimal values returned as floating
  point are rejected. The host must parse financial values as Decimal.
- Query submission supplies a unique Data API client token, allowing AWS SDK
  retries of the same request to be idempotent. Result access uses the same
  caller identity. The connector does not create shared Data API sessions.
- Raw driver errors, SQL text, parameters, AWS credentials, and result rows are
  excluded from execution logs. The larger application owns user authorization,
  its trusted query registry, credential-provider policy, workload budgets,
  network access, and client-specific configuration.

References: [Data API authentication](https://docs.aws.amazon.com/redshift-data/latest/APIReference/API_ExecuteStatement.html),
[Data API IAM permissions](https://docs.aws.amazon.com/redshift/latest/mgmt/data-api-iam.html),
[Redshift IAM resources](https://docs.aws.amazon.com/service-authorization/latest/reference/list_redshift.html),
[Serverless IAM resources](https://docs.aws.amazon.com/service-authorization/latest/reference/list_redshift-serverless.html),
and [database GRANT](https://docs.aws.amazon.com/redshift/latest/dg/r_GRANT.html).
