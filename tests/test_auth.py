import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from drivebridge.core import accounts, connect, connection_error, import_oauth_client, oauth_client_file

CLIENT = {'installed': {'client_id': 'test.apps.googleusercontent.com', 'client_secret': 'test',
    'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token',
    'redirect_uris': ['http://localhost']}}


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.temp.name, 'DRIVEBRIDGE_OAUTH_CLIENT': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop('DRIVEBRIDGE_OAUTH_CLIENT', None)

    def test_disabled_api_points_to_same_oauth_project(self):
        error = Exception('raw response')
        error.content = json.dumps({'error': {'message': 'Google Drive API has not been used in project 123456789012 before or it is disabled.', 'errors': [{'reason': 'accessNotConfigured'}]}}).encode()
        result = connection_error(error)
        self.assertEqual(result['title'], 'Ative a Google Drive API')
        self.assertTrue(result['url'].endswith('?project=123456789012'))
        self.assertNotIn('raw response', result['message'])

    def test_authorization_timeout_has_retry_instructions(self):
        result = connection_error(TimeoutError('timeout'))
        self.assertIsNone(result['url'])
        self.assertIn('Entrar com Google', result['message'])

    def test_configuration_imported_once_and_private(self):
        self.assertIsNone(oauth_client_file())
        source = Path(self.temp.name) / 'download.json'
        source.write_text(json.dumps(CLIENT))
        destination = import_oauth_client(source)
        source.unlink()
        self.assertEqual(oauth_client_file(), destination)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_distribution_client_used_without_personal_import(self):
        bundled = Path(self.temp.name) / 'app-client.json'
        bundled.write_text(json.dumps(CLIENT))
        with patch('drivebridge.core.bundled_oauth_client_file', return_value=bundled):
            self.assertEqual(oauth_client_file(), bundled)
            personal = Path(self.temp.name) / 'personal.json'
            personal.write_text(json.dumps(CLIENT))
            imported = import_oauth_client(personal)
            self.assertEqual(oauth_client_file(), imported)

    def test_explicit_configuration_does_not_silently_use_another_client(self):
        with patch.dict(os.environ, {'DRIVEBRIDGE_OAUTH_CLIENT': self.temp.name + '/missing.json'}):
            self.assertIsNone(oauth_client_file())

    def test_reject_service_account_and_non_google_endpoint(self):
        source = Path(self.temp.name) / 'bad.json'
        for data in ({'type': 'service_account'}, {'installed': dict(CLIENT['installed'], token_uri='https://example.com/token')}):
            source.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                import_oauth_client(source)
        self.assertIsNone(oauth_client_file())

    @patch('googleapiclient.discovery.build')
    @patch('google_auth_oauthlib.flow.InstalledAppFlow.from_client_config')
    def test_email_hint_and_actual_authorized_account_persisted(self, create_flow, build):
        source = Path(self.temp.name) / 'download.json'
        source.write_text(json.dumps(CLIENT))
        flow = MagicMock()
        create_flow.return_value = flow
        flow.run_local_server.return_value.to_json.return_value = '{"refresh_token":"fake-token"}'
        build.return_value.about.return_value.get.return_value.execute.return_value = {
            'user': {'emailAddress': 'authorized@example.com', 'displayName': 'Test'}}
        identifier = connect(source, 'suggested@example.com')
        options = flow.run_local_server.call_args.kwargs
        self.assertEqual(options['login_hint'], 'suggested@example.com')
        self.assertEqual(options['access_type'], 'offline')
        self.assertTrue(create_flow.call_args.kwargs['autogenerate_code_verifier'])
        self.assertEqual(accounts()[identifier]['emailAddress'], 'authorized@example.com')
        token = Path(self.temp.name) / 'drivebridge' / 'tokens' / (identifier + '.json')
        self.assertEqual(token.stat().st_mode & 0o777, 0o600)

    @patch('googleapiclient.discovery.build')
    @patch('google_auth_oauthlib.flow.InstalledAppFlow.from_client_config')
    def test_failed_login_does_not_mark_account_connected(self, create_flow, build):
        source = Path(self.temp.name) / 'download.json'
        source.write_text(json.dumps(CLIENT))
        create_flow.return_value.run_local_server.side_effect = TimeoutError('Authorization timed out')
        with self.assertRaises(TimeoutError):
            connect(source, 'user@example.com')
        self.assertEqual(accounts(), {})
        build.assert_not_called()
