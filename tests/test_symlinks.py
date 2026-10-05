import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from drivebridge.core import Drive, synchronize


class SymlinkTests(unittest.TestCase):
    def test_file_and_directory_links_are_preserved_and_not_transferred(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'local'
            root.mkdir()
            outside = Path(directory) / 'outside'
            outside.mkdir()
            secret = outside / 'file.txt'
            secret.write_text('unchanged')
            (root / 'Steam.dll').symlink_to(secret)
            (root / 'folder-link').symlink_to(outside, target_is_directory=True)
            (root / 'broken-link').symlink_to(outside / 'missing')
            checksum = 'unchanged-checksum'
            common = root / 'normal.txt'
            common.write_text('normal')
            from drivebridge.core import digest
            drive = Drive.__new__(Drive)
            drive.api = MagicMock()
            drive.children = lambda parent: [
                {'id': 'linked', 'name': 'Steam.dll', 'mimeType': 'application/octet-stream', 'size': '5', 'md5Checksum': checksum},
                {'id': 'folder', 'name': 'folder-link', 'mimeType': 'application/vnd.google-apps.folder'},
                {'id': 'common', 'name': 'normal.txt', 'mimeType': 'text/plain', 'size': '6', 'md5Checksum': digest(common)},
            ] if parent == 'root' else [
                {'id': 'nested', 'name': 'file.txt', 'mimeType': 'text/plain', 'size': '5', 'md5Checksum': checksum}]
            events = []
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}), patch('drivebridge.core.Drive', return_value=drive):
                synchronize({'local': str(root), 'account': 'test', 'remote': 'root', 'direction': 'both'},
                            lambda kind, data: events.append((kind, data)), threading.Event())
            self.assertEqual(secret.read_text(), 'unchanged')
            self.assertTrue((root / 'Steam.dll').is_symlink())
            self.assertTrue((root / 'folder-link').is_symlink())
            self.assertTrue((root / 'broken-link').is_symlink())
            drive.api.files.assert_not_called()
            self.assertIn('3 links locais', events[-1][1])
            self.assertIn('0 transferências', events[-1][1])
            self.assertTrue(any('Steam.dll' in data for kind, data in events if kind == 'log'))
