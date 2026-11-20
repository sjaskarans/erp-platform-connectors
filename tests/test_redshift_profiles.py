import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from erp_connectors.connections import ConnectionStore, ObjectSelectionInput, RedshiftConnectionInput, runtime_connector


def profile(**overrides):
    return RedshiftConnectionInput.model_validate({'platform': 'redshift', 'name': 'Offline Redshift',
        'region': 'us-east-1', 'deployment': 'serverless', 'database': 'dev',
        'workgroup_name': 'erp-test', 'aws_profile': 'erp-reader', 'objects': [], **overrides})


class NoSecretVault:
    def protect(self, _): raise AssertionError('AWS credentials must not be stored')
    def reveal(self, _): raise AssertionError('AWS credentials must not be loaded from profile storage')


class RedshiftProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = ConnectionStore(Path(self.folder.name) / 'profiles.sqlite3', vault=NoSecretVault())

    def test_profile_and_selection_contain_no_aws_credentials(self):
        saved = self.store.save(profile())
        self.assertFalse(saved['secret_configured'])
        config, secret, _ = self.store.runtime(saved['id'])
        self.assertEqual(secret, '')
        self.assertEqual(config['aws_profile'], 'erp-reader')
        self.assertEqual(self.store._row(saved['id'])['secret'], b'')
        selected = self.store.select_objects(saved['id'], ObjectSelectionInput(version=1, objects=[{
            'name': 'finance', 'catalog': 'dev', 'schema_name': 'approved', 'table_name': 'finance_lines',
            'preview_columns': ['amount']}]))
        self.assertEqual(selected['version'], 2)
        self.assertEqual(self.store.runtime(saved['id'])[1], '')
        with self.assertRaises(ValueError):
            self.store.select_objects(saved['id'], ObjectSelectionInput(version=1, objects=[]))

    def test_invalid_target_and_access_key_fields_are_rejected(self):
        for overrides in [{'cluster_identifier': 'cluster'}, {'workgroup_name': None},
                          {'deployment': 'provisioned'}, {'region': 'https://127.0.0.1'},
                          {'workgroup_name': 'https://localhost'}, {'aws_profile': '../../other'},
                          {'aws_access_key_id': 'do-not-store'}, {'aws_secret_access_key': 'do-not-store'},
                          {'db_user': 'admin'}, {'secret_arn': 'not-supported'}]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError): profile(**overrides)

    def test_provisioned_cluster_configuration(self):
        self.assertEqual(profile(deployment='provisioned', workgroup_name=None, cluster_identifier='erp-cluster').cluster_identifier, 'erp-cluster')

    def test_serverless_names_follow_the_distinct_aws_api_pattern(self):
        from erp_connectors import RedshiftConnector
        value = profile(workgroup_name='123-erp--test')
        client = RedshiftConnector(object(), value.database, {}, workgroup_name=value.workgroup_name)
        self.assertEqual(client.target, {'WorkgroupName': '123-erp--test'})

    def test_cross_database_and_long_utf8_identifiers_are_rejected(self):
        for obj in [dict(name='x', catalog='other', schema_name='s', table_name='t'),
                    dict(name='x', catalog='dev', schema_name='é' * 64, table_name='t')]:
            with self.assertRaises(ValueError): profile(objects=[obj])

    def test_runtime_uses_provider_chain_and_bounded_aws_client(self):
        for name in ['erp-reader', None]:
            with self.subTest(profile=name), patch('boto3.Session') as session:
                live = runtime_connector(profile(aws_profile=name).model_dump(), '')
                session.assert_called_once_with(profile_name=name, region_name='us-east-1')
                call = session.return_value.client.call_args
                self.assertEqual(call.args, ('redshift-data',))
                self.assertEqual(call.kwargs['config'].connect_timeout, 5)
                self.assertEqual(call.kwargs['config'].retries['total_max_attempts'], 2)
                self.assertEqual(live.target, {'WorkgroupName': 'erp-test'})
                self.assertNotIn('aws_secret_access_key', str(call.kwargs))

    def test_missing_profile_is_reported_without_raw_configuration(self):
        from botocore.exceptions import ProfileNotFound
        from erp_connectors import ConnectorError
        with patch('boto3.Session', side_effect=ProfileNotFound(profile='private-profile')), self.assertRaises(ConnectorError) as caught:
            runtime_connector(profile().model_dump(), '')
        self.assertEqual(caught.exception.code, 'AWS_CREDENTIALS_UNAVAILABLE')
        self.assertNotIn('private-profile', str(caught.exception))

    def test_selection_cannot_change_database(self):
        saved = self.store.save(profile())
        with self.assertRaises(ValueError):
            self.store.select_objects(saved['id'], ObjectSelectionInput(version=1, objects=[{
                'name': 'x', 'catalog': 'other', 'schema_name': 's', 'table_name': 't'}]))
        self.assertEqual(self.store.list()[0]['version'], 1)
