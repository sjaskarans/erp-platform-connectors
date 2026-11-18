"""Exercise the installed SDK's HTTP contract without contacting Databricks."""

import json
import unittest
from unittest.mock import patch

from databricks.sdk.core import ApiClient, Config
from requests import Response
from requests.adapters import BaseAdapter

from erp_connectors.databricks import DatabricksConnector
from test_databricks import success


class OfflineCredentials:
    def __call__(self, config):
        return lambda: {"Authorization": "Bearer offline-test-token"}

    def auth_type(self):
        return "offline-test"


class OfflineAdapter(BaseAdapter):
    def __init__(self):
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        response = Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(success()).encode()
        response.request = request
        return response

    def close(self):
        pass


class SDKContractTests(unittest.TestCase):
    def test_real_sdk_sends_expected_statement_request(self):
        adapter = OfflineAdapter()
        # Host discovery and credentials are isolated; the actual API client and
        # request serialization still run through the installed SDK and requests.
        with patch.object(Config, "_resolve_host_metadata"):
            config = Config(
                host="https://offline.cloud.databricks.com",
                credentials_strategy=OfflineCredentials(),
                http_adapter=adapter,
                http_timeout_seconds=15,
                retry_timeout_seconds=15,
            )
        client = DatabricksConnector(ApiClient(config), "warehouse", {})
        result = client.check_connection()
        self.assertEqual(result.rows, [["1"]])
        self.assertEqual(len(adapter.requests), 1)
        request = adapter.requests[0]
        self.assertEqual(request.url, "https://offline.cloud.databricks.com/api/2.0/sql/statements")
        self.assertEqual(request.headers["Authorization"], "Bearer offline-test-token")
        body = json.loads(request.body)
        self.assertEqual(body["wait_timeout"], "0s")
        self.assertEqual(body["warehouse_id"], "warehouse")
        self.assertEqual(body["format"], "JSON_ARRAY")


if __name__ == "__main__":
    unittest.main()
