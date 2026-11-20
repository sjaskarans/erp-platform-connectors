import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from erp_connectors.demo import main


class DemoEnvironmentTests(unittest.TestCase):
    def test_cloud_credentials_are_passed_only_to_connector_process(self):
        environment = {'AWS_ACCESS_KEY_ID': 'offline-only', 'AWS_SECRET_ACCESS_KEY': 'offline-only',
            'AWS_SESSION_TOKEN': 'offline-only', 'AWS_WEB_IDENTITY_TOKEN_FILE': 'offline-token-file',
            'BOTO_CONFIG': 'offline-config', 'AWS_PROFILE': 'erp-reader',
            'DATABRICKS_CLIENT_SECRET': 'offline-only', 'SNOWFLAKE_PRIVATE_KEY_FILE': 'offline-only',
            'ERP_API_TOKEN': 'offline-local-api-token-' * 2}
        child = MagicMock()
        child.poll.return_value = 0
        with patch.dict(os.environ, environment, clear=True), patch('sys.argv', ['demo', '--ui']), patch(
                'erp_connectors.demo.socket.socket'), patch('erp_connectors.demo.shutil.which', return_value='node'), patch(
                'pathlib.Path.is_file', return_value=True), patch('erp_connectors.demo.subprocess.Popen', return_value=child) as launch, redirect_stdout(io.StringIO()):
            main()
        calls = launch.call_args_list
        self.assertEqual(len(calls), 3)
        connector_env = calls[0].kwargs['env']
        self.assertEqual(connector_env['AWS_SECRET_ACCESS_KEY'], 'offline-only')
        for call in calls[1:]:
            process_env = call.kwargs['env']
            self.assertFalse(any(key.startswith(('AWS_', 'DATABRICKS_', 'SNOWFLAKE_')) for key in process_env))
            self.assertNotIn('BOTO_CONFIG', process_env)
            self.assertIn('ERP_API_TOKEN', process_env)
