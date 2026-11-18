import getpass
import os
import unittest
import warnings
from unittest.mock import patch

from erp_connectors.cli import load_connector, prompt_client_secret


class LocalAuthTests(unittest.TestCase):
    def test_prompt_never_puts_secret_in_environment(self):
        with patch.dict(os.environ, {"DATABRICKS_AUTH_TYPE": "oauth-m2m"}, clear=True):
            with patch("erp_connectors.cli.getpass.getpass", return_value="private-secret"):
                self.assertEqual(prompt_client_secret(), "private-secret")
            self.assertNotIn("DATABRICKS_CLIENT_SECRET", os.environ)

    def test_prompt_refuses_echoing_fallback(self):
        def unsafe_prompt(*args):
            warnings.warn("No terminal", getpass.GetPassWarning)
            self.fail("The insecure fallback must not proceed")
        with patch.dict(os.environ, {"DATABRICKS_AUTH_TYPE": "oauth-m2m"}):
            with patch("erp_connectors.cli.getpass.getpass", side_effect=unsafe_prompt):
                with self.assertRaises(getpass.GetPassWarning):
                    prompt_client_secret()

    def test_prompt_refuses_empty_secret(self):
        with patch.dict(os.environ, {"DATABRICKS_AUTH_TYPE": "oauth-m2m"}):
            with patch("erp_connectors.cli.getpass.getpass", return_value=""):
                with self.assertRaises(ValueError):
                    prompt_client_secret()

    def test_oidc_does_not_prompt_for_client_secret(self):
        with patch.dict(os.environ, {"DATABRICKS_AUTH_TYPE": "file-oidc"}):
            with patch("erp_connectors.cli.getpass.getpass") as prompt:
                with self.assertRaises(ValueError):
                    prompt_client_secret()
                prompt.assert_not_called()

    def test_explicit_secret_is_passed_to_sdk_without_environment_mutation(self):
        from pathlib import Path
        example = Path(__file__).resolve().parents[1] / "connector.example.json"
        environment = {
            "DATABRICKS_HOST": "https://123.4.gcp.databricks.com",
            "DATABRICKS_AUTH_TYPE": "oauth-m2m",
            "DATABRICKS_CLIENT_ID": "application-id",
            "DATABRICKS_SQL_WAREHOUSE_ID": "warehouse",
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch("databricks.sdk.core.Config") as config:
                with patch("databricks.sdk.WorkspaceClient"):
                    load_connector(str(example), client_secret="private-secret")
            self.assertEqual(config.call_args.kwargs["client_secret"], "private-secret")
            self.assertEqual(config.call_args.kwargs["auth_type"], "oauth-m2m")
            self.assertNotIn("DATABRICKS_CLIENT_SECRET", os.environ)


if __name__ == "__main__":
    unittest.main()
