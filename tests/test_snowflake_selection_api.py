import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from erp_connectors.connections import BrowseInput, ConnectionStore, ObjectSelectionInput
from erp_connectors.models import ConnectorError
from erp_connectors.service import create_app
from test_snowflake_discovery import MemoryVault
from test_snowflake_profiles import snowflake_profile


class SelectionAPITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = ConnectionStore(Path(self.folder.name) / 'profiles.sqlite3', vault=MemoryVault())
        self.profile = self.store.save(snowflake_profile())
        with patch.dict(os.environ, {'ERP_API_TOKEN': 'test-only-' * 6}), patch(
                'erp_connectors.service.ConnectionStore', return_value=self.store):
            self.app = create_app(connector=object())
        self.select = next(r.endpoint for r in self.app.routes if r.path.endswith('/selection'))
        self.browse = next(r.endpoint for r in self.app.routes if r.path.endswith('/browse'))

    def test_selection_validates_columns_before_persisting_and_retains_key(self):
        secret = self.store.runtime(self.profile['id'])[1]
        body = ObjectSelectionInput(version=1, objects=snowflake_profile().objects)
        live = SimpleNamespace(objects={}, describe=MagicMock(return_value=SimpleNamespace(rows=[['AMOUNT']])))
        with patch('erp_connectors.service.runtime_connector', return_value=live):
            saved = self.select(self.profile['id'], body)
        self.assertEqual(saved['version'], 2)
        self.assertEqual(self.store.runtime(saved['id'])[1], secret)
        live.describe.assert_called_once_with('finance')

    def test_unavailable_columns_do_not_change_selection(self):
        body = ObjectSelectionInput(version=1, objects=snowflake_profile().objects)
        live = SimpleNamespace(objects={}, describe=MagicMock(return_value=SimpleNamespace(rows=[['OTHER_COLUMN']])))
        with patch('erp_connectors.service.runtime_connector', return_value=live), self.assertRaises(HTTPException) as error:
            self.select(self.profile['id'], body)
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.store.list()[0]['version'], 1)

    def test_stale_selection_does_not_make_cloud_calls(self):
        with patch('erp_connectors.service.runtime_connector') as runtime, self.assertRaises(HTTPException) as error:
            self.select(self.profile['id'], ObjectSelectionInput(version=2, objects=[]))
        self.assertEqual(error.exception.status_code, 409)
        runtime.assert_not_called()

    def test_browse_returns_paginated_metadata_and_no_secret(self):
        expected = {'items': [{'name': 'ERP'}], 'next_cursor': None, 'prefix_case_sensitive': True}
        live = SimpleNamespace(browse=MagicMock(return_value=expected))
        with patch('erp_connectors.service.runtime_connector', return_value=live):
            result = self.browse(self.profile['id'], BrowseInput(level='databases'))
        self.assertEqual(result, expected)
        self.assertNotIn('private_key', str(result))
        live.browse.assert_called_once_with(level='databases', database='', schema_name='', table='', after='', prefix='')

    def test_browse_returns_safe_error_code(self):
        live = SimpleNamespace(browse=MagicMock(side_effect=ConnectorError('SNOWFLAKE_KEY_AUTHENTICATION_FAILED')))
        with patch('erp_connectors.service.runtime_connector', return_value=live), self.assertRaises(HTTPException) as error:
            self.browse(self.profile['id'], BrowseInput(level='databases'))
        self.assertEqual(error.exception.status_code, 502)
        self.assertEqual(error.exception.detail['code'], 'SNOWFLAKE_KEY_AUTHENTICATION_FAILED')
