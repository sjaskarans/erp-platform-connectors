import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from erp_connectors.connections import ConnectionStore, SnowflakeConnectionInput, private_key_der, runtime_connector
from test_connections import profile


def fixture_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"offline-test-passphrase")).decode()


def snowflake_profile(pem=None, **overrides):
    return SnowflakeConnectionInput.model_validate({
        "platform": "snowflake", "name": "Offline Snowflake", "account": "org-account",
        "warehouse": "ERP_API_WH", "username": "SVC_ERP_CONNECTOR", "role": "ERP_CONNECTOR_READER",
        "private_key_pem": pem or fixture_key(), "private_key_passphrase": "offline-test-passphrase",
        "objects": [{"name": "finance", "catalog": "ERP_DEMO", "schema_name": "APPROVED",
                     "table_name": "FINANCE_LINES", "preview_columns": ["AMOUNT"]}], **overrides,
    })


class SnowflakeIdentityTests(unittest.TestCase):
    def test_encrypted_key_is_validated_and_wrong_passphrase_rejected(self):
        pem = fixture_key()
        self.assertIsInstance(private_key_der(pem, "offline-test-passphrase"), bytes)
        with self.assertRaises(ValueError):
            private_key_der(pem, "incorrect")
        with self.assertRaises(ValueError):
            private_key_der("invalid-key")

    def test_runtime_uses_service_key_and_explicit_role_with_qmark_binding(self):
        value = snowflake_profile()
        public = value.model_dump(exclude={"private_key_pem", "private_key_passphrase"})
        secret = json.dumps({"private_key_pem": value.private_key_pem.get_secret_value(),
                             "private_key_passphrase": "offline-test-passphrase"})
        with patch("snowflake.connector.connect") as connect:
            client = runtime_connector(public, secret)
            client.connection_factory()
        options = connect.call_args.kwargs
        self.assertEqual(options["authenticator"], "SNOWFLAKE_JWT")
        self.assertEqual(options["role"], "ERP_CONNECTOR_READER")
        self.assertEqual(options["paramstyle"], "qmark")
        self.assertIsInstance(options["private_key"], bytes)
        self.assertNotIn("password", options)
        self.assertEqual(options["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"], 120)


@unittest.skipUnless(os.name == "nt", "Local vault is Windows-specific")
class SnowflakeProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "profiles.sqlite3"
        self.store = ConnectionStore(self.path)

    def test_secret_bundle_is_encrypted_and_not_in_profile_responses(self):
        value = snowflake_profile()
        saved = self.store.save(value)
        data = json.dumps(self.store.list())
        for word in ("private_key_pem", "private_key_passphrase", "offline-test-passphrase", "BEGIN ENCRYPTED"):
            self.assertNotIn(word, data)
        self.assertNotIn(b"offline-test-passphrase", self.path.read_bytes())
        config, secret, _ = self.store.runtime(saved["id"])
        self.assertEqual(config["platform"], "snowflake")
        self.assertEqual(json.loads(secret)["private_key_pem"], value.private_key_pem.get_secret_value())

    def test_edit_retains_credentials_and_cannot_change_provider(self):
        saved = self.store.save(snowflake_profile())
        old_secret = self.store.runtime(saved["id"])[1]
        self.store.save(snowflake_profile(name="Updated", private_key_pem=None, private_key_passphrase=None), saved["id"])
        self.assertEqual(self.store.runtime(saved["id"])[1], old_secret)
        with self.assertRaises(ValueError):
            self.store.save(profile(), saved["id"])

    def test_missing_key_generates_backend_identity_and_setup_is_stable(self):
        saved = self.store.save(snowflake_profile(private_key_pem=None, private_key_passphrase=None))
        setup = self.store.snowflake_setup(saved["id"])
        self.assertEqual(setup, self.store.snowflake_setup(saved["id"]))
        self.assertIn('TYPE = SERVICE', setup['sql'])
        self.assertIn('GRANT SELECT ON TABLE "ERP_DEMO"."APPROVED"."FINANCE_LINES"', setup['sql'])
        self.assertNotIn('ALL TABLES', setup['sql'])
        self.assertNotIn('PRIVATE', json.dumps(setup))
        self.assertNotIn('private_key', json.dumps(saved))
        secret = json.loads(self.store.runtime(saved['id'])[1])['private_key_pem']
        self.assertNotIn(secret.encode(), self.path.read_bytes())
        key = serialization.load_pem_private_key(secret.encode(), password=None)
        self.assertEqual(key.key_size, 3072)
        import base64
        self.assertEqual(key.public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo), base64.b64decode(setup['public_key']))

    def test_setup_quotes_identifiers_and_literals(self):
        value = snowflake_profile(private_key_pem=None, private_key_passphrase=None,
                                 role='reader"quoted', warehouse="warehouse'quoted",
                                 objects=[{"name": "view", "catalog": "DB", "schema_name": "S",
                                           "table_name": 'a"b', "object_kind": "VIEW"}])
        saved = self.store.save(value)
        sql = self.store.snowflake_setup(saved['id'])['sql']
        self.assertIn('"reader""quoted"', sql)
        self.assertIn("'warehouse''quoted'", sql)
        self.assertIn('GRANT SELECT ON VIEW "DB"."S"."a""b"', sql)

    def test_invalid_keys_cannot_be_saved(self):
        with self.assertRaises(ValueError):
            self.store.save(snowflake_profile(pem="invalid"))

    def test_databricks_and_snowflake_profiles_remain_separate(self):
        db = self.store.save(profile())
        sf = self.store.save(snowflake_profile())
        self.assertEqual(self.store.runtime(db["id"])[1], "disposable-secret-value")
        self.assertIn("private_key_pem", self.store.runtime(sf["id"])[1])
        self.assertEqual({p["platform"] for p in self.store.list()}, {"databricks", "snowflake"})
