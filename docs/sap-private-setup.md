# S/4HANA Private Edition / on-premises: client onboarding

This is the selected deployment for this project. Use this guide with
[the SAP connector contract](sap-setup.md). The existing OData adapter supports
this route when the selected SAP service exposes supported v2/v4 metadata and
entity collections. A live client acceptance test is still required.

## 1. Agree the access scope with the SAP team

The client's SAP Basis and security administrators should record:

| Setting | Required decision |
| --- | --- |
| System | S/4HANA release, sandbox/non-production system and three-digit client |
| Gateway | Embedded in S/4HANA or a separate Gateway hub |
| Business scope | Required entities, fields, company codes and other organizational restrictions |
| API | Supported/released OData service and its v2/v4 version |
| Endpoint | Exact HTTPS service root, including the correct host, port and path |
| Identity | Dedicated technical user or supported certificate/token mapping |
| Connectivity | Direct corporate network, VPN, or an explicitly configured host transport |
| Trust | Certificate chain trusted by the Python backend |

The connector needs an OData endpoint, not an SAP GUI connection string or direct
database access. Use the client's supported APIs; do not expose arbitrary SAP
tables simply to make discovery possible. One profile targets one service root.

## 2. Enable the service — SAP administrator

1. Select the appropriate API for the installed S/4HANA release. Identify its
   technical service or service group and required business authorizations.
2. For **OData v2**, register/activate the selected service in
   `/IWFND/MAINT_SERVICE`, using the correct backend system alias. Confirm its
   required ICF service is active and reachable over HTTPS.
3. For **OData v4**, use `/IWFND/V4_ADMIN` to publish the selected service group
   and configure its routing/system alias. Do not guess a v4 URL by editing a v2
   URL; copy the published service root for the selected version.
4. Confirm that `$metadata` returns XML and the desired entity set supports JSON
   reads. Do this initially in the SAP tools, then with the connector identity.

SAP documents [v2/v4 service administration and publishing](https://help.sap.com/docs/ABAP_PLATFORM_BW4HANA/68bf513362174d54b58cddec28794093/54dcfc66cece463e9e25bc17cf5117b9.html).
Activation/publishing permissions belong to the administrator, not the connector.

## 3. Create a dedicated technical user — SAP security administrator

1. In the appropriate SAP client, open `SU01` and create a dedicated integration
   identity, for example `SVC_ERP_CONNECTOR`, if the naming policy permits it.
2. Choose a non-dialog technical user type compatible with the selected HTTP
   authentication and the client's policy. The administrator owns the user-type,
   password lifetime, locking, rotation and audit decisions.
3. For the current reference form, configure user/password authentication over
   HTTPS only if permitted by the endpoint's security policy. Supply the password
   through the client's secret manager or directly into the local setup form.
4. If Gateway and S/4HANA are separate systems, explicitly configure the intended
   backend identity and routing. A Gateway login alone does not establish backend
   business permissions. Any trusted RFC/identity propagation is managed inside
   the SAP landscape; the Python connector still calls OData over HTTPS.

SAP's [technical communication user guidance](https://help.sap.com/docs/advanced-financial-closing/administration/how-to-create-technical-communication-user-erp)
uses `SU01`; its [separate Gateway/backend guidance](https://help.sap.com/docs/advanced-financial-closing/administration/how-to-create-technical-communication-users-separate-gateway-and-business-back-ends?state=PRODUCTION&version=SHIP)
illustrates why both layers' identities and authorizations must be considered.
Use the requirements for the selected API rather than copying that product's roles.

## 4. Assign least-privilege roles — SAP security administrator

1. Create a customer PFCG role, e.g. `Z_ERP_CONNECTOR_READ`, in the appropriate
   system/client. Add the **specific service's authorization defaults** using the
   relevant service entries. Maintain the generated authorizations and profile.
2. Apply the service start authorizations appropriate to the Gateway release and
   OData version. V2 commonly uses `S_SERVICE`; V4 uses `S_START`. Do not substitute
   a wildcard grant for configuring the actual service/service group.
3. Add the business authorizations the service's data provider requires. Restrict
   organizational fields to the agreed scope and display/read activities where
   supported. There is no universal role granting read-only access to all SAP ERP.
4. Assign the generated role to the technical user and perform user comparison.
   For a separate hub/backend, maintain the required roles in each system.
5. Check that writes are denied by source permissions or by a suitable read-only
   service design. The connector only sends GETs, but that does not make an
   otherwise powerful SAP user read-only.

Use SAP's [service authorization role instructions](https://help.sap.com/docs/ABAP_PLATFORM_NEW/a602ff71a47c441bb3000504ec938fea/2dc7493959bb4e18a91eb8c225647884.html?locale=en-US&version=202110.002)
and [v4 authorization differences](https://help.sap.com/docs/SAP_NETWEAVER_AS_ABAP_751_IP/68bf513362174d54b58cddec28794093/c6dd838722ce4b8d9cc4a0741d93d864.html?locale=en-US).
Role names above are examples to create, not SAP-delivered roles. No SAP_ALL,
Gateway administrator role, or service activation privileges are needed by the
connector merely to consume an already configured API.

## 5. Make the endpoint reachable from the backend

Run the Python backend on a machine with the required network route, usually the
corporate network or approved VPN. The Next.js browser does not connect directly
to SAP. A local browser cannot turn an unreachable SAP server into a reachable one.

For standard HTTPS trust and permitted Basic authentication, the existing
reference form is sufficient. If the company uses a private certificate authority,
an explicit proxy, client certificates or a managed token flow, configure those
in the hosting application's injected session, for example:

```python
session = requests.Session()
session.trust_env = False
session.verify = r"C:\client-config\sap-ca-chain.pem"  # Approved CA bundle.
session.auth = (technical_user, secret_manager_password)
# Only if required by the agreed network route:
# session.proxies = {"https": "http://approved-proxy.example:8080"}
# For supported mTLS instead of Basic, configure session.cert and SAP mapping.
connector = SAPConnector(session, approved_root, approved_objects,
                         sap_client="100", odata_version="2", owns_session=True)
```

This excerpt assumes the imports from the main SAP guide. Certificate/proxy file
paths are trusted backend settings; the reference form does not accept them. Keep
certificate validation enabled. Cloud Connector requires a separately configured
host/BTP integration; it is not automatically used by a local Python process.

## 6. Connect and select data

1. The backend operator adds the exact service root to
   `ERP_SAP_ALLOWED_SERVICE_ROOTS`, as shown in the main guide, and restarts the
   connector process. The browser cannot authorize a new destination itself.
2. In **Add connection → SAP OData**, enter the service root, version, SAP client,
   technical username and password. Do not append `sap-client` to the service URL;
   use the separate client field.
3. Save and test. An empty selection establishes metadata connectivity only.
4. Browse the service namespace and entity sets, choose the required scalar
   fields, save, test again and preview a small number of rows.
5. Compare sample records and scoped totals with SAP. Preserve identifiers,
   company keys, currencies and units in the upstream semantic model.

## 7. Accept the integration

Verify allowed records and denied organizational scope using the technical
identity. A zero-row response alone does not establish that the permissions are
correct. Exercise multiple pages and compare complete query results before
feeding them into business metrics or AI.

For failures, the SAP team can use `STAUTHTRACE` and `/IWFND/ERROR_LOG` to identify
the missing service/business permission or routing issue, then add only the
required authorization. SAP describes these [Private Edition/on-premises diagnostic tools](https://help.sap.com/docs/SAP_SUS_SDI/b15a1be1941c44d4be60ba206536283d/22c72f83cbd34b9ea927bcda05723906.html).
The connector returns sanitized errors; it does not broaden the SAP role itself.

Do not run the Public Edition communication-arrangement walkthrough as the
default procedure for this deployment. The actual API may have additional setup
requirements, which the client's SAP administrator should follow for its release.
