import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from erp_connectors.connections import ConnectionInput, ConnectionStore


def profile(**overrides):
    return ConnectionInput.model_validate({
        "name": "Offline setup test", "workspace_url": "https://12345.7.gcp.databricks.com",
        "warehouse_id": "1234567890abcdef", "client_id": "11111111-1111-1111-1111-111111111111",
        "client_secret": "disposable-secret-value", "objects": [{"name": "finance_lines",
            "catalog": "demo", "schema_name": "approved", "table_name": "finance_lines",
            "preview_columns": ["company_code"]}], **overrides,
    })


class ConnectionValidationTests(unittest.TestCase):
    def test_workspace_validation_rejects_non_databricks_destinations(self):
        for url in ("http://12345.7.gcp.databricks.com", "https://127.0.0.1", "https://accounts.gcp.databricks.com",
                    "https://12345.7.gcp.databricks.com.evil.test", "https://u:p@12345.7.gcp.databricks.com",
                    "https://12345.7.gcp.databricks.com/path", "https://12345.7.gcp.databricks.com:8080"):
            with self.assertRaises(ValidationError):
                profile(workspace_url=url)

    def test_secrets_are_redacted_in_model_repr(self):
        self.assertNotIn("disposable-secret-value", repr(profile()))


@unittest.skipUnless(os.name == "nt", "Local vault is Windows-specific")
class ConnectionStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "profiles.sqlite3"
        self.store = ConnectionStore(self.path)

    def test_stored_secret_is_encrypted_and_never_in_public_profiles(self):
        saved = self.store.save(profile())
        self.assertNotIn("client_secret", saved)
        self.assertNotIn("disposable-secret-value", json.dumps(self.store.list()))
        self.assertNotIn(b"disposable-secret-value", self.path.read_bytes())
        self.assertEqual(self.store.runtime(saved["id"])[1], "disposable-secret-value")

    def test_profiles_persist_across_store_instances(self):
        saved = self.store.save(profile())
        reopened = ConnectionStore(self.path)
        self.assertEqual(reopened.list()[0]["id"], saved["id"])

    def test_each_connection_uses_its_own_configuration_and_secret(self):
        first = self.store.save(profile())
        second = self.store.save(profile(name="Second", warehouse_id="abcdef1234567890", client_secret="different-secret"))
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.store.runtime(second["id"])[0]["warehouse_id"], "abcdef1234567890")
        self.assertEqual(self.store.runtime(first["id"])[1], "disposable-secret-value")
        self.assertEqual(self.store.runtime(second["id"])[1], "different-secret")

    def test_blank_update_preserves_secret_but_resets_verified_status(self):
        saved = self.store.save(profile())
        checked = self.store.record_test(saved["id"], saved["version"])
        self.assertEqual(checked["status"], "verified")
        updated = self.store.save(profile(name="Changed", client_secret=None), saved["id"])
        self.assertEqual(updated["status"], "saved")
        self.assertIsNone(updated["checked_at"])
        self.assertEqual(self.store.runtime(saved["id"])[1], "disposable-secret-value")

    def test_rotation_replaces_secret(self):
        saved = self.store.save(profile())
        self.store.save(profile(client_secret="rotated-secret"), saved["id"])
        self.assertEqual(self.store.runtime(saved["id"])[1], "rotated-secret")

    def test_stale_test_cannot_verify_updated_profile(self):
        saved = self.store.save(profile())
        self.store.save(profile(name="Changed"), saved["id"])
        with self.assertRaises(ValueError):
            self.store.record_test(saved["id"], saved["version"])

    def test_test_failure_and_delete(self):
        saved = self.store.save(profile())
        failed = self.store.record_test(saved["id"], saved["version"], "OBJECT_NOT_VISIBLE")
        self.assertEqual(failed["status"], "failed")
        self.store.delete(saved["id"])
        self.assertEqual(self.store.list(), [])
        with self.assertRaises(KeyError):
            self.store.runtime(saved["id"])

    def test_new_profile_requires_secret(self):
        with self.assertRaises(ValueError):
            self.store.save(profile(client_secret=None))
