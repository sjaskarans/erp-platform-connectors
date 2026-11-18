# Create the Google Cloud Databricks test environment

Cloud resources have **not** been created by this project. You can already use
[the local demo](testing-demo.md) without cloud billing. The path below is for
testing the actual Google Cloud Databricks connection.

## 1. Check account eligibility and billing

For the Google Cloud Marketplace subscription path, Databricks documents a
Google billing account, appropriate billing/project permissions, and a project
linked to billing. The trial converts to pay-as-you-go when it expires. Review
the live offer, pricing, and cancellation terms before subscribing; do not assume
the Google Cloud free-credit balance covers Databricks charges.

A personal Google Cloud project alone may not satisfy all prerequisites. The
documented classic-workspace setup also calls for Google Workspace or Cloud
Identity and an organization. Check the current signup requirements for your
account before changing an existing personal project or creating an organization.

[Official Google Cloud subscription and trial requirements](https://docs.databricks.com/gcp/en/admin/account-settings-gcp/create-subscription)

## 2. Create the Databricks account

1. Open Google Cloud Marketplace and find the official Databricks listing.
2. Select the intended billing-linked project.
3. Review the offer and billing terms, and subscribe if acceptable.
4. Choose **Register with Databricks**, complete the Google sign-in, and activate
   the account using **Manage on Provider** as described by the signup guide.
5. Confirm you can open `https://accounts.gcp.databricks.com` as the account owner.

The billing and sign-in steps require your account. Never send Google passwords,
payment details, recovery codes, or Databricks OAuth secrets in chat.

## 3. Create one development workspace

If a serverless workspace is offered for your account and region, the documented
path is **Workspaces → Create workspace → select region → Use serverless compute
and default storage**. Wait until it reaches Running. This avoids provisioning a
classic workspace's VPC, NAT, and storage yourself.

Availability varies, and current workspace documentation labels the serverless
creation option Public Preview. If your account does not offer it, use the
documented classic-workspace path after checking project permissions and quotas.
Do not grant broad cloud roles to the read-only connector to perform setup.

[Workspace choices](https://docs.databricks.com/gcp/en/admin/workspace),
[serverless creation steps](https://docs.databricks.com/gcp/en/getting-started/starter-journey/infra-setup/create-workspaces/gcp/serverless),
[classic workspace creation](https://docs.databricks.com/gcp/en/admin/workspace/create-workspace).

## 4. Create/select SQL compute and a catalog

1. Open the workspace and copy its exact HTTPS URL.
2. Open **SQL Warehouses**. Use an existing suitable warehouse or create a small
   test warehouse supported by your account. Set short auto-stop and review
   size/scaling and cost controls.
3. Record its warehouse ID from the connection details.
4. In Catalog Explorer, select an existing Unity Catalog catalog in which your
   setup user can create a schema. A workspace catalog may already be available.
5. If no usable catalog exists, have the account/metastore administrator complete
   Unity Catalog and managed-storage setup before creating these test tables.

Serverless usage and default storage are billed by Databricks; Google Cloud
credits or discounts do not apply to that usage. External storage and networking
can have separate cloud-provider costs.
[Serverless billing details](https://docs.databricks.com/gcp/en/admin/workspace/serverless-workspaces)

## 5. Create the data and read-only identity

1. Generate and execute the synthetic setup SQL as described in
   [the demo guide](testing-demo.md).
2. Create a Databricks service principal and generate its OAuth M2M secret.
3. Grant CAN USE on the SQL warehouse and SELECT plus parent usage privileges
   on the dedicated demo objects. Apply the generated reader-grant template.
4. Configure the local connector and enter the secret at its hidden prompt.

Detailed identity/permission steps are in [the Databricks setup
guide](databricks-setup.md). Keep the setup owner's create/write privileges
separate from the connector's read permissions.

## 6. Verify and control the test environment

Run the connection check, metadata discovery, small preview, and registered
fixture queries. Reconcile values and types with `demo/generated/expected.json`.
Verify effective permissions and credential rotation. Review compute usage and
stop unused compute. Clean up only the dedicated test objects when finished.

The non-secret details needed for the next step are the workspace URL, SQL
warehouse ID, actual catalog name, and reader principal application ID.
