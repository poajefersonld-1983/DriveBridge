import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from drivebridge.core import Drive, FOLDER, synchronize


def drive_with(children):
    drive = Drive.__new__(Drive)
    drive.children = lambda parent: children[parent]
    return drive


class NativeDocumentTests(unittest.TestCase):
    def test_nested_native_document_does_not_stop_normal_files(self):
        drive = drive_with({'root': [{'id': 'folder', 'name': 'Condominio', 'mimeType': FOLDER}],
            'folder': [{'id': 'doc', 'name': 'STV CONTATO', 'mimeType': 'application/vnd.google-apps.document'},
                       {'id': 'pdf', 'name': 'contrato.pdf', 'mimeType': 'application/pdf', 'size': '3', 'md5Checksum': 'abc'}]})
        skipped = {}
        files, folders = drive.scan('root', threading.Event(), skipped=skipped)
        self.assertEqual(set(files), {'Condominio/contrato.pdf'})
        self.assertEqual(set(skipped), {'Condominio/STV CONTATO'})
        self.assertEqual(folders['Condominio'], 'folder')

    def test_shortcuts_and_native_incompatible_names_are_skipped(self):
        drive = drive_with({'root': [{'id': 'doc', 'name': 'A/B', 'mimeType': 'application/vnd.google-apps.spreadsheet'},
                                    {'id': 'link', 'name': 'Shortcut', 'mimeType': 'application/vnd.google-apps.shortcut'}]})
        skipped = {}
        files, _ = drive.scan('root', threading.Event(), skipped=skipped)
        self.assertFalse(files)
        self.assertEqual(len(skipped), 2)

    def test_real_sync_pipeline_downloads_binary_and_does_not_upload_native_collision(self):
        data = b'pdf-content'
        checksum = hashlib.md5(data).hexdigest()
        drive = drive_with({'root': [{'id': 'doc', 'name': 'STV CONTATO', 'mimeType': 'application/vnd.google-apps.document'},
            {'id': 'pdf', 'name': 'contrato.pdf', 'mimeType': 'application/pdf', 'size': str(len(data)), 'md5Checksum': checksum}]})
        from unittest.mock import MagicMock
        drive.api = MagicMock()
        class Downloader:
            def __init__(self, stream, request, chunksize):
                self.stream = stream
            def next_chunk(self, num_retries):
                self.stream.write(data)
                return None, True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'local'
            root.mkdir()
            original = root / 'STV CONTATO'
            original.write_text('local version')
            events = []
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}), patch('drivebridge.core.Drive', return_value=drive), patch('googleapiclient.http.MediaIoBaseDownload', Downloader):
                synchronize({'account': 'test', 'local': str(root), 'remote': 'root', 'direction': 'both'},
                            lambda kind, value: events.append((kind, value)), threading.Event())
            self.assertEqual((root / 'contrato.pdf').read_bytes(), data)
            self.assertEqual(original.read_text(), 'local version')
            drive.api.files.return_value.create.assert_not_called()
            drive.api.files.return_value.update.assert_not_called()
            self.assertIn('1 transferências', events[-1][1])
            self.assertIn('1 itens remotos não transferidos', events[-1][1])
            progress = [value for kind, value in events if kind == 'progress']
            self.assertEqual(progress[-1][:2], (len(data), len(data)))

    def test_duplicate_binary_names_are_skipped_without_choosing_arbitrarily(self):
        item = {'id': 'a', 'name': 'duplicate', 'mimeType': 'text/plain'}
        drive = drive_with({'root': [item, dict(item, id='b')]})
        skipped = {}
        files, _ = drive.scan('root', threading.Event(), skipped=skipped)
        self.assertFalse(files)
        self.assertIn('duplicate', skipped)
