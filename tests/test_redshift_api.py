import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from erp_connectors import ConnectorError
from erp_connectors.connections import BrowseInput, ConnectionStore, ObjectSelectionInput
from erp_connectors.service import create_app
from test_redshift_profiles import NoSecretVault, profile


class RedshiftAPITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = ConnectionStore(Path(self.folder.name) / 'profiles.sqlite3', vault=NoSecretVault())
        self.saved = self.store.save(profile())
        with patch.dict(os.environ, {'ERP_API_TOKEN': 'offline-api-test-' * 3}), patch(
                'erp_connectors.service.ConnectionStore', return_value=self.store):
            self.app = create_app(connector=object())
        self.test = next(r.endpoint for r in self.app.routes if r.path.endswith('/test'))
        self.browse = next(r.endpoint for r in self.app.routes if r.path.endswith('/browse'))
        self.select = next(r.endpoint for r in self.app.routes if r.path.endswith('/selection'))

    def test_empty_selection_verifies_identity_only_and_closes_client(self):
        live = SimpleNamespace(check_connection=MagicMock(), close=MagicMock())
        with patch('erp_connectors.service.runtime_connector', return_value=live):
            checked = self.test(self.saved['id'])
        self.assertEqual(checked['status'], 'connected')
        self.assertFalse(checked['secret_configured'])
        live.close.assert_called_once()

    def test_credential_failure_is_visible_in_profile_status(self):
        with patch('erp_connectors.service.runtime_connector', side_effect=ConnectorError('AWS_CREDENTIALS_UNAVAILABLE')):
            checked = self.test(self.saved['id'])
        self.assertEqual(checked['status'], 'failed')
        self.assertEqual(checked['error_code'], 'AWS_CREDENTIALS_UNAVAILABLE')

    def test_metadata_and_selection_use_shared_api_and_close_clients(self):
        live = SimpleNamespace(browse=MagicMock(return_value={'items': [{'name': 'dev'}],
            'next_cursor': None, 'prefix_case_sensitive': True}), objects={},
            describe=MagicMock(return_value=SimpleNamespace(rows=[['amount']])), close=MagicMock())
        with patch('erp_connectors.service.runtime_connector', return_value=live):
            page = self.browse(self.saved['id'], BrowseInput(level='databases'))
            saved = self.select(self.saved['id'], ObjectSelectionInput(version=1, objects=[{
                'name': 'finance', 'catalog': 'dev', 'schema_name': 'approved', 'table_name': 'finance_lines',
                'preview_columns': ['amount']}]))
        self.assertEqual(page['items'], [{'name': 'dev'}])
        self.assertEqual(saved['version'], 2)
        self.assertEqual(live.close.call_count, 2)

    def test_invalid_database_selection_is_400_and_does_not_persist(self):
        live = SimpleNamespace(objects={}, describe=MagicMock(side_effect=ValueError('Invalid database')), close=MagicMock())
        with patch('erp_connectors.service.runtime_connector', return_value=live), self.assertRaises(HTTPException) as error:
            self.select(self.saved['id'], ObjectSelectionInput(version=1, objects=[{
                'name': 'x', 'catalog': 'other', 'schema_name': 's', 'table_name': 't'}]))
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.store.list()[0]['version'], 1)
        live.close.assert_called_once()

    def test_clear_selection_does_not_request_aws_credentials(self):
        with patch('erp_connectors.service.runtime_connector') as runtime:
            self.select(self.saved['id'], ObjectSelectionInput(version=1, objects=[]))
        runtime.assert_not_called()
