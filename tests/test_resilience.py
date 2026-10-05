import errno
import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from drivebridge.core import Drive, FOLDER, scan_local, synchronize


class ResilienceTests(unittest.TestCase):
    def test_bad_remote_name_and_unreadable_folder_do_not_stop_other_files(self):
        drive = Drive.__new__(Drive)
        normal = {'id': 'ok', 'name': 'ok.bin', 'mimeType': 'application/octet-stream', 'size': '2', 'md5Checksum': 'sum'}
        def children(parent):
            if parent == 'restricted':
                raise PermissionError('Folder denied')
            return [normal, dict(normal, name='A/B'), {'id':'restricted','name':'restricted','mimeType':FOLDER}]
        drive.children = children
        skipped = {}
        files, _ = drive.scan('root', threading.Event(), skipped=skipped)
        self.assertEqual(set(files), {'ok.bin'})
        self.assertEqual(set(skipped), {'A/B', 'restricted'})

    def test_local_permission_error_reserves_name_and_keeps_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bad').write_text('bad')
            (root / 'good').write_text('good')
            def checksum(path, cancel):
                if path.name == 'bad':
                    raise PermissionError('No read access')
                return 'sum'
            with patch('drivebridge.core.digest', side_effect=checksum):
                files, blocked, _ = scan_local(root, lambda *_: None, threading.Event(), lambda _: False)
            self.assertEqual(set(files), {'good'})
            self.assertEqual(blocked, {'bad'})

    def run_downloads(self, fault):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / 'local'
        root.mkdir()
        (root / 'a-bad').write_bytes(b'original')
        content = b'good'
        checksum = hashlib.md5(content).hexdigest()
        drive = Drive.__new__(Drive)
        drive.api = MagicMock()
        drive.children = lambda _: [{'id': name, 'name': name, 'mimeType': 'text/plain', 'size': '4', 'md5Checksum': checksum} for name in ['a-bad', 'b-good']]
        drive.api.files.return_value.get_media.side_effect = lambda fileId: fileId
        class Downloader:
            def __init__(self, stream, request, chunksize):
                self.stream, self.request = stream, request
            def next_chunk(self, num_retries):
                if self.request == 'a-bad':
                    self.stream.write(b'part')
                    if isinstance(fault, Exception):
                        raise fault
                    return SimpleNamespace(resumable_progress=4), True
                self.stream.write(content)
                return SimpleNamespace(resumable_progress=4), True
        events = []
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory.name}), patch('drivebridge.core.Drive', return_value=drive), patch('googleapiclient.http.MediaIoBaseDownload', Downloader):
            if isinstance(fault, OSError) and fault.errno == errno.ENOSPC:
                with self.assertRaises(OSError):
                    synchronize({'local':str(root),'remote':'root','account':'test','direction':'download'}, lambda k,v: events.append((k,v)),threading.Event())
            else:
                synchronize({'local':str(root),'remote':'root','account':'test','direction':'download'}, lambda k,v: events.append((k,v)),threading.Event())
        self.assertEqual((root / 'a-bad').read_bytes(), b'original')
        self.assertFalse(list(root.glob('.drivebridge-*.part')))
        return root, events

    def test_single_file_error_continues_and_reports_partial_progress(self):
        root, events = self.run_downloads(PermissionError('File unavailable'))
        self.assertEqual((root / 'b-good').read_bytes(), b'good')
        self.assertIn('1 falhas pendentes', events[-1][1])
        last = [v for k,v in events if k == 'progress'][-1]
        self.assertEqual(last[:2], (4, 8))

    def test_checksum_failure_preserves_original_and_transfer_rates(self):
        root, events = self.run_downloads(None)
        self.assertEqual((root / 'b-good').read_bytes(), b'good')
        progress = [v for k,v in events if k == 'progress']
        self.assertEqual(progress[-1][:2], (4, 8))
        self.assertEqual(progress[-1][7], 8)
        self.assertEqual(sorted(v[7] for v in progress), [v[7] for v in progress])

    def test_full_disk_stops_instead_of_failing_every_file(self):
        root, _ = self.run_downloads(OSError(errno.ENOSPC, 'No space left'))
        # Uma transferência já em voo pode terminar antes de o coordenador detectar a falta de espaço.
        if (root / 'b-good').exists():
            self.assertEqual((root / 'b-good').read_bytes(), b'good')
