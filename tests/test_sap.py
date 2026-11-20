import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from requests.adapters import BaseAdapter
from requests.models import Response

from erp_connectors import ConnectorError, DataObject, ODataFilter, ODataQueryTemplate, SAPConnector
from erp_connectors.sap import service_root

ROOT = "https://sap.example/sap/opu/odata/sap/ERP/"
FIXTURES = Path(__file__).resolve().parents[1] / "demo/sap"
XML = (FIXTURES / "metadata-v2.xml").read_bytes()
OBJECT = DataObject("service", "ERP_DEMO", "FinanceLines", ("CompanyCode", "DocumentNumber", "Amount", "Currency"))


class Transport(BaseAdapter):
    """Use requests' actual URL/auth preparation without opening a network socket."""
    def __init__(self, responses):
        self.responses, self.calls, self.closed = list(responses), [], False

    def send(self, request, **kwargs):
        self.calls.append((request, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        status, body = value if isinstance(value, tuple) else (200, value)
        response = Response()
        response.status_code, response.url, response.request = status, request.url, request
        response._content = body if isinstance(body, bytes) else json.dumps(body).encode()
        response._content_consumed = True
        return response

    def close(self): self.closed = True


def connector(*responses, version="2", **limits):
    session = requests.Session()
    session.trust_env = False
    session.auth = ("offline-service-user", "offline-fixture-password")
    metadata = XML if version == "2" else XML.replace(b"http://schemas.microsoft.com/ado/2008/09/edm", b"http://docs.oasis-open.org/odata/ns/edm")
    transport = Transport([metadata, *responses])
    session.mount("https://", transport)
    sdk = SAPConnector(session, ROOT, {"finance": OBJECT}, odata_version=version, owns_session=True, **limits)
    return sdk, transport


class SAPTests(unittest.TestCase):
    def test_discovery_describe_and_scalar_fields(self):
        sdk, transport = connector()
        self.assertEqual(sdk.check_connection().metadata["verification"], "metadata_access_only")
        self.assertEqual(sdk.browse("databases")["items"], [{"name": "service"}])
        self.assertEqual(sdk.browse("schemas", database="service")["items"], [{"name": "ERP_DEMO"}])
        self.assertEqual(sdk.browse("objects", database="service", schema_name="ERP_DEMO")["items"], [{"name": "FinanceLines", "kind": "ENTITY"}])
        names = {r[0] for r in sdk.describe("finance").rows}
        self.assertNotIn("ToCompany", names)
        amount = next(p for p in sdk.describe("finance").metadata["properties"] if p["name"] == "Amount")
        self.assertEqual((amount["precision"], amount["scale"]), ("23", "2"))
        self.assertEqual(len(transport.calls), 1)
        sdk.close()
        self.assertTrue(transport.closed)

    def test_v2_paging_preserves_money_and_leading_zeroes(self):
        sdk, t = connector((FIXTURES / "finance-v2-page1.json").read_bytes(), (FIXTURES / "finance-v2-page2.json").read_bytes())
        result = sdk.preview("finance", 10)
        self.assertEqual(len(result.rows), 3)
        self.assertEqual(result.rows[1], ["1000", "0000000002", "9007199254740993.25", "INR"])
        first = parse_qs(urlsplit(t.calls[1][0].url).query)
        self.assertEqual(first["$select"], ["CompanyCode,DocumentNumber,Amount,Currency"])
        self.assertEqual(first["$top"], ["11"])
        self.assertEqual(len(t.calls), 3)  # requests must not follow another destination
        self.assertTrue(all(r.method == "GET" for r, _ in t.calls))

    def test_v4_numeric_tokens_are_not_float_rounded(self):
        sdk, t = connector(b'{"value":[{"CompanyCode":"1000","DocumentNumber":"0001","Amount":9007199254740993.25,"Currency":null}]}', version="4")
        result = sdk.preview("finance")
        self.assertEqual(result.rows[0][2:], ["9007199254740993.25", None])
        self.assertIn("IEEE754Compatible=true", t.calls[-1][0].headers["Accept"])

    def test_v4_pagination_and_metadata_version(self):
        sdk, t = connector({"value": [], "@odata.nextLink": "FinanceLines?$skiptoken=2"}, {"value": []}, version="4")
        self.assertEqual(sdk.preview("finance").rows, [])
        self.assertEqual(len(t.calls), 3)
        sdk, t = connector(version="4")
        t.responses[0] = XML
        with self.assertRaises(ConnectorError) as error: sdk.check_connection()
        self.assertEqual(error.exception.code, "SAP_METADATA_INVALID")

    def test_explicit_sap_client_is_kept_on_metadata_and_every_page(self):
        sdk, t = connector({"d": {"results": [], "__next": "FinanceLines?$skiptoken=2"}}, {"d": {"results": []}}, sap_client="100")
        sdk.preview("finance")
        self.assertTrue(all(parse_qs(urlsplit(r.url).query)["sap-client"] == ["100"] for r, _ in t.calls))
        sdk, _ = connector({"d": {"results": [], "__next": "FinanceLines?sap-client=200"}}, sap_client="100")
        with self.assertRaises(ConnectorError) as error: sdk.preview("finance")
        self.assertEqual(error.exception.code, "SAP_UNSAFE_NEXT_LINK")

    def test_deadline_fails_before_network_request(self):
        sdk, t = connector()
        with self.assertRaises(ConnectorError) as error:
            sdk._get(ROOT, deadline=0, budget=[0])
        self.assertEqual(error.exception.code, "SAP_TIMEOUT")
        self.assertEqual(len(t.calls), 0)

    def test_registered_filters_are_typed_and_escaped(self):
        sdk, t = connector({"d": {"results": []}})
        sdk.queries["company"] = ODataQueryTemplate("finance", ("Amount",), (ODataFilter("CompanyCode", "eq", "company"),))
        sdk.execute("company", {"company": "O'Reilly & $expand=Secret"})
        params = parse_qs(urlsplit(t.calls[-1][0].url).query)
        self.assertEqual(params["$filter"], ["CompanyCode eq 'O''Reilly & $expand=Secret'"])
        self.assertNotIn("$expand", params)
        with self.assertRaises(ValueError): sdk.execute("company", {"company": "1000", "sql": "anything"})
        with self.assertRaises(KeyError): sdk.execute("unknown", {})

    def test_decimal_and_integer_literal_validation(self):
        sdk, _ = connector()
        self.assertEqual(sdk._literal("200.00", "Edm.Decimal"), "200.00M")
        self.assertEqual(sdk._literal("9223372036854775807", "Edm.Int64"), "9223372036854775807L")
        for val, typ in [("1 or true", "Edm.Int32"), ("256", "Edm.Byte"), ("NaN", "Edm.Decimal"), ("bad", "Edm.Guid")]:
            with self.assertRaises(ValueError): sdk._literal(val, typ)

    def test_cross_origin_and_other_resource_next_links_are_rejected(self):
        for link in ["https://attacker.example/FinanceLines", "//attacker.example/", "../Other", "FinanceLines/Other?$x=1", "FinanceLines#fragment", "FinanceLines%2fOther", "https://user:pass@sap.example/", "FinanceLines\\evil"]:
            sdk, t = connector({"d": {"results": [], "__next": link}})
            with self.subTest(link=link), self.assertRaises(ConnectorError) as error: sdk.preview("finance")
            self.assertEqual(error.exception.code, "SAP_UNSAFE_NEXT_LINK")
            self.assertEqual(len(t.calls), 2)

    def test_redirects_and_source_error_text_are_not_exposed(self):
        for status, code in [(302, "SAP_REQUEST_FAILED"), (401, "SAP_AUTHENTICATION_FAILED"), (403, "SAP_ACCESS_DENIED"), (404, "SAP_SERVICE_NOT_FOUND"), (429, "SAP_THROTTLED")]:
            sdk, _ = connector((status, b"private source failure and credentials"))
            with self.assertRaises(ConnectorError) as error: sdk.preview("finance")
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("private source", str(error.exception))

    def test_budgets_loop_and_truncation(self):
        batch = {"d": {"results": [{"Amount": str(i)} for i in range(3)]}}
        sdk, _ = connector(batch)
        sdk.objects["finance"] = DataObject("service", "ERP_DEMO", "FinanceLines", ("Amount",))
        self.assertTrue(sdk.preview("finance", 2).truncated)
        sdk, _ = connector({"d": {"results": [], "__next": "FinanceLines"}})
        with self.assertRaises(ConnectorError) as error: sdk.preview("finance")
        self.assertEqual(error.exception.code, "SAP_PAGINATION_LOOP")
        sdk, _ = connector({"d": {"results": [], "__next": "FinanceLines?$skiptoken=1"}}, max_pages=1)
        with self.assertRaises(ConnectorError) as error: sdk.preview("finance")
        self.assertEqual(error.exception.code, "RESULT_PAGE_LIMIT")
        sdk, _ = connector(batch, max_rows=1)
        sdk.objects["finance"] = DataObject("service", "ERP_DEMO", "FinanceLines", ("Amount",))
        with self.assertRaises(ConnectorError) as error: sdk.preview("finance")
        self.assertEqual(error.exception.code, "RESULT_ROW_LIMIT")
        sdk, _ = connector(max_bytes=10)
        with self.assertRaises(ConnectorError) as error: sdk.check_connection()
        self.assertEqual(error.exception.code, "RESULT_BYTE_LIMIT")

    def test_malformed_response_and_network_failure_are_sanitized(self):
        for response in [b"html login", {"d": {"results": [{"Amount": {"nested": 1}}]}}, {"d": {"results": [{}]}}, {"d": {"results": {}}}]:
            sdk, _ = connector(response)
            with self.assertRaises(ConnectorError) as error: sdk.preview("finance")
            self.assertEqual(error.exception.code, "SAP_RESPONSE_INVALID")
        sdk, _ = connector(requests.ConnectionError("contains credentials"))
        with self.assertRaises(ConnectorError) as error: sdk.preview("finance")
        self.assertEqual(error.exception.code, "SAP_NETWORK_ERROR")

    def test_xml_external_entity_and_dtd_are_rejected(self):
        sdk, t = connector()
        t.responses[0] = b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>'
        with self.assertRaises(ConnectorError) as error: sdk.check_connection()
        self.assertEqual(error.exception.code, "SAP_METADATA_INVALID")

    def test_invalid_service_roots_and_unknown_properties(self):
        for root in ["http://sap.example/api/", "https://u:p@sap.example/api/", "https://sap.example/api/?x=y", "https://sap.example/a/../b", "https://sap.example/a%2fb", "https://sap.example/api/#x"]:
            with self.assertRaises(ValueError): service_root(root)
        sdk, _ = connector()
        sdk.objects["finance"] = DataObject("service", "ERP_DEMO", "FinanceLines", ("ToCompany",))
        with self.assertRaises(ValueError): sdk.preview("finance")
        with self.assertRaises(ValueError): sdk.preview("finance", 101)


if __name__ == "__main__": unittest.main()
