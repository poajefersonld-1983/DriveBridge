import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from drivebridge.core import Drive, FOLDER, synchronize


class ParallelTests(unittest.TestCase):
    def test_concurrent_uploads_share_one_parent_and_have_separate_clients(self):
        barrier = threading.Barrier(4)
        mutex = threading.Lock()
        creation_count = 0
        client_ids = set()
        original_drive = Drive
        payload = b'content'
        checksum = hashlib.md5(payload).hexdigest()
        class Upload:
            def __init__(self, identifier):
                self.identifier = identifier
            def next_chunk(self, num_retries):
                with mutex:
                    client_ids.add(self.identifier)
                barrier.wait(timeout=5)
                return None, {'id':'uploaded', 'size':str(len(payload)), 'md5Checksum':checksum}
        def factory(account):
            obj = original_drive.__new__(original_drive)
            obj.children = lambda parent: []
            obj.api = MagicMock()
            def create(body, **kwargs):
                nonlocal creation_count
                if body.get('mimeType') == FOLDER:
                    with mutex:
                        creation_count += 1
                    request = MagicMock()
                    request.execute.return_value = {'id':'parent'}
                    return request
                self.assertEqual(body['parents'], ['parent'])
                return Upload(id(obj))
            obj.api.files.return_value.create.side_effect = create
            return obj
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'local'
            (root / 'folder').mkdir(parents=True)
            for n in range(4):
                (root / 'folder' / f'{n}.txt').write_bytes(payload)
            events=[]
            with patch.dict(os.environ, {'XDG_CONFIG_HOME':directory}), patch('drivebridge.core.Drive', side_effect=factory):
                synchronize({'local':str(root),'remote':'root','account':'test','direction':'upload'}, lambda k,v: events.append((k,v)),threading.Event())
            self.assertEqual(creation_count,1)
            self.assertEqual(len(client_ids),4)
            self.assertIn('4 transferências; 0 falhas',events[-1][1])
            progress=[v for k,v in events if k == 'progress']
            self.assertEqual(progress[-1][:2], (4*len(payload),4*len(payload)))
            self.assertTrue(any(len(v[8]) == 4 for v in progress))
            self.assertEqual([v[7] for v in progress],sorted(v[7] for v in progress))
            self.assertTrue(all(v[0] <= v[1] for v in progress))
            states=list((Path(directory)/'drivebridge/states').glob('*.json'))
            import json
            self.assertEqual(len(json.loads(states[0].read_text())),4)

    def test_cancelled_parallel_downloads_clean_temporary_files(self):
        original_drive=Drive
        cancel=threading.Event()
        barrier=threading.Barrier(4)
        checksum=hashlib.md5(b'valid').hexdigest()
        class Downloader:
            def __init__(self,stream,request,chunksize):
                self.stream=stream
            def next_chunk(self,num_retries):
                self.stream.write(b'partial')
                barrier.wait(timeout=5)
                cancel.set()
                return SimpleNamespace(resumable_progress=2),False
        def factory(account):
            obj=original_drive.__new__(original_drive)
            obj.children=lambda parent: [{'id':str(n),'name':f'{n}.bin','mimeType':'application/octet-stream','size':'5','md5Checksum':checksum} for n in range(4)]
            obj.api=MagicMock()
            return obj
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'local'
            root.mkdir()
            with patch.dict(os.environ,{'XDG_CONFIG_HOME':directory}),patch('drivebridge.core.Drive',side_effect=factory),patch('googleapiclient.http.MediaIoBaseDownload',Downloader):
                with self.assertRaises(InterruptedError):
                    synchronize({'local':str(root),'remote':'root','account':'test','direction':'download'},lambda *_:None,cancel)
            self.assertFalse(list(root.iterdir()))
