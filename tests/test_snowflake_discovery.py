import tempfile
import unittest
from pathlib import Path

from snowflake.connector.cursor import ResultMetadata

from erp_connectors.connections import BrowseInput, ConnectionStore, ObjectSelectionInput
from erp_connectors.models import ConnectorError
from test_snowflake import Connection, Cursor, connector
from test_snowflake_profiles import snowflake_profile


class DiscoveryTests(unittest.TestCase):
    def cursor(self, rows, names):
        cursor = Cursor(rows)
        cursor.description = [ResultMetadata(name, 2, None, None, None, None, True) for name in names]
        return cursor

    def test_database_pagination_does_not_skip_the_lookahead(self):
        rows = [(f"DB{i:03}",) for i in range(101)]
        conn = Connection(self.cursor(rows, ["name"]))
        page = connector(conn).browse("databases")
        self.assertEqual(len(page['items']), 100)
        self.assertEqual(page['next_cursor'], 'DB099')
        next_conn = Connection(self.cursor([('DB100',)], ['name']))
        tail = connector(next_conn).browse('databases', after=page['next_cursor'])
        self.assertEqual(tail['items'], [{'name': 'DB100'}])
        self.assertIsNone(tail['next_cursor'])
        self.assertIn("LIMIT 101 FROM 'DB099'", next_conn.primary.calls[0][0])

    def test_show_result_columns_are_resolved_by_name(self):
        conn = Connection(self.cursor([('timestamp', 'ERP_DEMO')], ['created_on', 'name']))
        self.assertEqual(connector(conn).browse('databases')['items'], [{'name': 'ERP_DEMO'}])

    def test_database_prefix_and_cursor_are_escaped(self):
        conn = Connection(self.cursor([], ['name']))
        connector(conn).browse('databases', prefix="a' OR 1=1 --", after="a\\'b")
        sql, params = conn.primary.calls[0]
        self.assertIn("STARTS WITH 'a'' OR 1=1 --'", sql)
        self.assertIn("FROM 'a\\\\''b'", sql)
        self.assertEqual(params, ())

    def test_table_metadata_is_scoped_and_values_are_bound(self):
        conn = Connection(self.cursor([('FINANCE', 'BASE TABLE'), ('TOTALS', 'VIEW')], ['TABLE_NAME', 'TABLE_TYPE']))
        result = connector(conn).browse('objects', database='a"b', schema_name="s' OR 1=1 --", prefix='F', after='A')
        sql, params = conn.primary.calls[0]
        self.assertIn('"a""b".INFORMATION_SCHEMA.TABLES', sql)
        self.assertNotIn("s' OR", sql)
        self.assertEqual(params, ("s' OR 1=1 --", 'A', 'F'))
        self.assertEqual(result['items'], [{'name': 'FINANCE', 'kind': 'TABLE'}, {'name': 'TOTALS', 'kind': 'VIEW'}])

    def test_columns_and_schemas_are_metadata_only(self):
        for level, rows, names in (
            ('schemas', [('APPROVED',)], ['SCHEMA_NAME']),
            ('columns', [('AMOUNT', 'NUMBER')], ['COLUMN_NAME', 'DATA_TYPE']),
        ):
            with self.subTest(level=level):
                conn = Connection(self.cursor(rows, names))
                page = connector(conn).browse(level, database='ERP', schema_name='S', table='T')
                self.assertEqual(page['items'][0]['name'], rows[0][0])
                self.assertIn('.INFORMATION_SCHEMA.', conn.primary.calls[0][0])
                self.assertNotIn('SELECT *', conn.primary.calls[0][0])

    def test_invalid_requests_do_not_open_connections(self):
        conn = Connection()
        for args, kwargs in (
            (('raw-sql',), {}), (('schemas',), {}),
            (('objects',), {'database': 'D'}),
            (('columns',), {'database': 'D', 'schema_name': 'S'}),
            (('databases',), {'prefix': '\n'}),
            (('databases',), {'after': 'x' * 256}),
        ):
            with self.assertRaises(ValueError):
                connector(conn).browse(*args, **kwargs)
        self.assertEqual(conn.cursor_count, 0)

    def test_show_output_without_expected_metadata_fails_closed(self):
        conn = Connection(self.cursor([('unexpected',)], ['wrong']))
        with self.assertRaises(ConnectorError):
            connector(conn).browse('databases')

    def test_request_rejects_sql_and_unknown_fields(self):
        with self.assertRaises(ValueError):
            BrowseInput.model_validate({'level': 'objects', 'sql': 'SELECT * FROM secret'})


class MemoryVault:
    def __init__(self):
        self.values = {}

    def protect(self, value):
        token = str(len(self.values)).encode()
        self.values[token] = value
        return token

    def reveal(self, value):
        return self.values[value]


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = ConnectionStore(Path(self.folder.name) / 'test.sqlite3', vault=MemoryVault())

    def test_empty_bootstrap_then_selection_preserves_identity_and_key(self):
        saved = self.store.save(snowflake_profile(private_key_pem=None, private_key_passphrase=None, objects=[]))
        self.assertEqual(saved['objects'], [])
        self.assertEqual(self.store.record_test(saved['id'], saved['version'], has_objects=False)['status'], 'connected')
        secret = self.store.runtime(saved['id'])[1]
        fingerprint = self.store.snowflake_setup(saved['id'])['fingerprint']
        objects = snowflake_profile().objects
        selected = self.store.select_objects(saved['id'], ObjectSelectionInput(version=saved['version'], objects=objects))
        self.assertEqual(selected['version'], saved['version'] + 1)
        self.assertEqual(selected['status'], 'saved')
        self.assertEqual(self.store.runtime(saved['id'])[1], secret)
        self.assertEqual(self.store.snowflake_setup(saved['id'])['fingerprint'], fingerprint)
        with self.assertRaises(ValueError):
            self.store.select_objects(saved['id'], ObjectSelectionInput(version=saved['version'], objects=[]))
        self.assertEqual(self.store.list()[0]['objects'], selected['objects'])

    def test_schema_permission_scope_is_explicit_and_has_no_future_grants(self):
        saved = self.store.save(snowflake_profile(private_key_pem=None, private_key_passphrase=None,
            objects=[], access_scope={'catalog': 'ERP', 'schema_name': 'CLIENT_APPROVED'}))
        sql = self.store.snowflake_setup(saved['id'])['sql']
        self.assertIn('SELECT ON ALL TABLES IN SCHEMA "ERP"."CLIENT_APPROVED"', sql)
        self.assertIn('SELECT ON ALL VIEWS IN SCHEMA "ERP"."CLIENT_APPROVED"', sql)
        self.assertNotIn('ON FUTURE', sql)
        self.assertNotIn('ALL TABLES IN DATABASE', sql)
        self.assertNotIn('CREATE DATABASE', sql)

    def test_selection_cannot_change_identity_or_secret(self):
        with self.assertRaises(ValueError):
            ObjectSelectionInput.model_validate({'version': 1, 'objects': [], 'role': 'ACCOUNTADMIN'})


if __name__ == '__main__':
    unittest.main()
