import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from erp_connectors import ConnectorError
from erp_connectors.connections import (BrowseInput, ConnectionStore, ObjectSelectionInput,
                                       SAPConnectionInput, connection_request_body, runtime_connector)
from erp_connectors.service import create_app
from test_sap import OBJECT, ROOT, connector


class TestVault:
    # Deliberately reversible test double; production always uses Windows DPAPI.
    def protect(self, value): return value.encode()[::-1]
    def reveal(self, value): return value[::-1].decode()


def profile(**overrides):
    return SAPConnectionInput.model_validate({"platform": "sap", "name": "Offline SAP", "service_url": ROOT,
                "odata_version": "2", "username": "SVC_OFFLINE", "password": "offline-password", **overrides})


def selection(**overrides):
    return {"name": "finance", "catalog": "service", "schema_name": "ERP_DEMO", "table_name": "FinanceLines",
            "object_kind": "ENTITY", "preview_columns": ["Amount", "Currency"], **overrides}


class SAPProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "profiles.db"
        self.store = ConnectionStore(self.path, vault=TestVault())

    def test_secret_is_encrypted_excluded_and_retained_when_editing(self):
        saved = self.store.save(profile())
        self.assertNotIn("password", saved)
        self.assertNotIn(b"offline-password", self.path.read_bytes())
        self.assertEqual(self.store.runtime(saved["id"])[1], "offline-password")
        self.store.save(profile(password=None, name="Renamed"), saved["id"])
        self.assertEqual(self.store.runtime(saved["id"])[1], "offline-password")
        self.assertEqual(connection_request_body(profile())["password"], "offline-password")
        with self.assertRaises(ValueError): self.store.save(profile(password=None))
        with self.assertRaises(ValueError): self.store.save(profile(password=None, username="OTHER"), saved["id"])
        with self.assertRaises(ValueError): self.store.save(profile(password=None, service_url="https://other.example/service/"), saved["id"])

    def test_exact_service_root_requires_operator_approval_before_session_creation(self):
        for allowed in ["[]", '"not a list"', "invalid-json", '["https://sap.example/other/"]']:
            with patch.dict(os.environ, {"ERP_SAP_ALLOWED_SERVICE_ROOTS": allowed}), patch("requests.Session") as session:
                with self.assertRaises(ConnectorError) as error: runtime_connector(profile().model_dump(), "offline-password")
                self.assertEqual(error.exception.code, "SAP_SERVICE_NOT_APPROVED")
                session.assert_not_called()
        with patch.dict(os.environ, {"ERP_SAP_ALLOWED_SERVICE_ROOTS": '["' + ROOT + '"]'}), patch("requests.Session") as session:
            live = runtime_connector(profile().model_dump(), "offline-password")
            self.assertEqual(session.return_value.auth, ("SVC_OFFLINE", "offline-password"))
            self.assertFalse(session.return_value.trust_env)
            live.close()
            session.return_value.close.assert_called_once()

    def test_entity_selection_is_versioned_and_rejects_warehouse_objects(self):
        saved = self.store.save(profile())
        selected = self.store.select_objects(saved["id"], ObjectSelectionInput(version=1, objects=[selection()]))
        self.assertEqual(selected["version"], 2)
        self.assertEqual(self.store.runtime(saved["id"])[1], "offline-password")
        for obj in [selection(catalog="other"), selection(object_kind="TABLE"), selection(table_name="FinanceLines/$value")]:
            with self.assertRaises(ValueError): profile(objects=[obj])
        with self.assertRaises(ValueError): self.store.select_objects(saved["id"], ObjectSelectionInput(version=1, objects=[]))

    def test_api_browse_select_test_and_preview_contract(self):
        saved = self.store.save(profile())
        with patch.dict(os.environ, {"ERP_API_TOKEN": "offline-api-test-" * 3}), patch("erp_connectors.service.ConnectionStore", return_value=self.store):
            app = create_app(connector=object())
        endpoint = lambda suffix: next(r.endpoint for r in app.routes if r.path.endswith(suffix))
        live, transport = connector({"d": {"results": [{"Amount": "200.00", "Currency": "INR"}]}})
        live.objects = {}
        with patch("erp_connectors.service.runtime_connector", return_value=live):
            checked = endpoint("/test")(saved["id"])
            self.assertEqual(checked["status"], "connected")
            page = endpoint("/browse")(saved["id"], BrowseInput(level="objects", database="service", schema_name="ERP_DEMO"))
            self.assertEqual(page["items"][0]["kind"], "ENTITY")
            selected = endpoint("/selection")(saved["id"], ObjectSelectionInput(version=1, objects=[selection()]))
        self.assertEqual(selected["version"], 2)
        # Selection does not silently enable different preview fields in a stale client.
        live.objects["finance"] = type(OBJECT)("service", "ERP_DEMO", "FinanceLines", ("Amount", "Currency"))
        with patch("erp_connectors.service.runtime_connector", return_value=live):
            checked = endpoint("/test")(saved["id"])
        self.assertEqual(checked["status"], "verified")
        self.assertTrue(transport.closed)

    def test_api_denial_preserves_saved_identity(self):
        saved = self.store.save(profile())
        with patch.dict(os.environ, {"ERP_API_TOKEN": "offline-api-test-" * 3}), patch("erp_connectors.service.ConnectionStore", return_value=self.store):
            app = create_app(connector=object())
        test = next(r.endpoint for r in app.routes if r.path.endswith("/test"))
        with patch("erp_connectors.service.runtime_connector", side_effect=ConnectorError("SAP_ACCESS_DENIED")):
            checked = test(saved["id"])
        self.assertEqual((checked["status"], checked["error_code"]), ("failed", "SAP_ACCESS_DENIED"))
        self.assertEqual(self.store.runtime(saved["id"])[1], "offline-password")
