"""Run from the source checkout after installation; never contacts a SAP host."""
import json
from dataclasses import asdict
from pathlib import Path

import requests
from requests.adapters import BaseAdapter
from requests.models import Response

from erp_connectors import DataObject, ODataQueryTemplate, SAPConnector

fixtures = Path(__file__).resolve().parents[1] / "demo/sap"


class FixtureTransport(BaseAdapter):
    def send(self, request, **kwargs):
        if request.url.endswith("/$metadata"):
            filename = "metadata-v2.xml"
        elif "$skiptoken=page2" in request.url:
            filename = "finance-v2-page2.json"
        else:
            filename = "finance-v2-page1.json"
        response = Response()
        response.status_code, response.url, response.request = 200, request.url, request
        response._content = (fixtures / filename).read_bytes()
        response._content_consumed = True
        return response

    def close(self): pass


def main():
    session = requests.Session()
    session.trust_env = False
    session.mount("https://", FixtureTransport())
    fields = ("CompanyCode", "DocumentNumber", "Amount", "Currency")
    sdk = SAPConnector(session, "https://offline.example.invalid/erp/",
        {"finance": DataObject("service", "ERP_DEMO", "FinanceLines", fields)},
        {"all_finance": ODataQueryTemplate("finance", fields)}, owns_session=True)
    try:
        print(json.dumps(asdict(sdk.execute("all_finance", {})), indent=2))
    finally:
        sdk.close()


if __name__ == "__main__": main()
