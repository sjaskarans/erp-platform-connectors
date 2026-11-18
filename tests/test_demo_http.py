"""End-to-end requests through two real loopback HTTP processes."""

import os
import secrets
import socket
import subprocess
import sys
import time
import unittest
import tempfile
from pathlib import Path

import requests


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DemoHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = secrets.token_urlsafe(32)
        port = free_port()
        backend_port = free_port()
        while port == backend_port:
            backend_port = free_port()
        cls.url = f"http://127.0.0.1:{port}"
        cls.headers = {"Authorization": f"Bearer {cls.token}"}
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        env = {**os.environ, "ERP_API_TOKEN": cls.token,
               "ERP_CONNECTOR_MODE": "local-demo", "ERP_CONNECTOR_PORT": str(backend_port),
               "ERP_CONNECTION_DB": str(Path(cls.folder.name) / "profiles.sqlite3")}
        cls.children = []
        cls.addClassCleanup(cls.stop_services)
        for module, selected_port in (("service", backend_port), ("gateway", port)):
            cls.children.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", f"erp_connectors.{module}:create_app",
                 "--factory", "--host", "127.0.0.1", "--port", str(selected_port), "--no-access-log"],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if any(child.poll() is not None for child in cls.children):
                raise RuntimeError("A test HTTP service failed to start")
            try:
                if cls.get("/health").status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.2)
        raise RuntimeError("Test HTTP services did not become ready")

    @classmethod
    def stop_services(cls):
        for child in cls.children:
            if child.poll() is None:
                child.terminate()
        for child in cls.children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

    @classmethod
    def get(cls, path):
        return requests.get(cls.url + path, headers=cls.headers, timeout=5,
                            proxies={"http": "", "https": ""})

    def post(self, query, body):
        return requests.post(self.url + "/v1/queries/" + query, headers=self.headers,
                             json=body, timeout=5, proxies={"http": "", "https": ""})

    def test_api_requires_token(self):
        response = requests.get(self.url + "/v1/objects", timeout=5,
                                proxies={"http": "", "https": ""})
        self.assertEqual(response.status_code, 401)

    def test_object_discovery_and_typed_preview(self):
        self.assertEqual(len(self.get("/v1/objects").json()["objects"]), 9)
        response = self.get("/v1/objects/finance_lines/preview?limit=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["platform"], "local-duckdb-demo")
        self.assertEqual(len(response.json()["rows"]), 2)

    def test_parameterized_query_through_gateway(self):
        response = self.post("overdue_receivables", {"parameters": {
            "company_code": "1000", "as_of_date": "2026-11-18"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rows"], [["INR", "600.00"], ["USD", "50.00"]])

    def test_raw_sql_body_is_rejected(self):
        response = self.post("finance_by_currency", {"parameters": {"company_code": "1000"},
                                                     "sql": "DROP TABLE finance_lines"})
        self.assertEqual(response.status_code, 422)

    def test_invalid_objects_and_limits_fail(self):
        self.assertEqual(self.get("/v1/objects/unknown/schema").status_code, 404)
        self.assertEqual(self.get("/v1/objects/finance_lines/preview?limit=101").status_code, 422)
        self.assertEqual(self.post("unknown", {"parameters": {}}).status_code, 404)

    def test_connection_create_list_update_delete_never_returns_secret(self):
        from test_connections import profile
        values = profile().model_dump(exclude={"client_secret"})
        values["client_secret"] = "disposable-http-secret"
        response = requests.post(self.url + "/v1/connections", headers=self.headers,
                                 json=values, timeout=5, proxies={"http": "", "https": ""})
        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertEqual(created["status"], "saved")
        self.assertNotIn("client_secret", created)
        listing = self.get("/v1/connections")
        self.assertNotIn("disposable-http-secret", listing.text)
        del values["client_secret"]
        values["name"] = "Changed HTTP profile"
        updated = requests.put(self.url + "/v1/connections/" + created["id"], headers=self.headers,
                               json=values, timeout=5, proxies={"http": "", "https": ""})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["name"], "Changed HTTP profile")
        deleted = requests.delete(self.url + "/v1/connections/" + created["id"], headers=self.headers,
                                  timeout=5, proxies={"http": "", "https": ""})
        self.assertEqual(deleted.status_code, 200)

    def test_connection_validation_does_not_echo_submitted_secret(self):
        response = requests.post(self.url + "/v1/connections", headers=self.headers,
                                 json={"client_secret": "sensitive-do-not-echo"}, timeout=5,
                                 proxies={"http": "", "https": ""})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("sensitive-do-not-echo", response.text)


if __name__ == "__main__":
    unittest.main()
