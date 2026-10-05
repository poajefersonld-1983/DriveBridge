"""Transferência de um arquivo; cada trabalhador usa sua própria conexão HTTP."""
import uuid
import os
from datetime import datetime, timezone
from pathlib import Path
from .core import FOLDER, TRANSFER_CHUNK_SIZE, digest


def transfer_file(drive, task, root, local, remote, folders, folder_lock, failed_folders, progress, cancel, chunk_size=TRANSFER_CHUNK_SIZE):
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    action, name, size = task
    path = root / name
    if cancel.is_set():
        raise InterruptedError('Cancelado')
    if action == 'upload':
        parent_name = Path(name).parent.as_posix()
        parent_name = '' if parent_name == '.' else parent_name
        current = ''
        # A criação de pastas é serializada; os dados seguem em paralelo.
        with folder_lock:
            for part in parent_name.split('/') if parent_name else []:
                if cancel.is_set():
                    raise InterruptedError('Cancelado')
                next_path = current + ('/' if current else '') + part
                if next_path in failed_folders:
                    raise RuntimeError('Pasta remota pendente; tente novamente na próxima execução.')
                if next_path not in folders:
                    try:
                        folders[next_path] = drive.api.files().create(body={'name': part, 'mimeType': FOLDER,
                            'parents': [folders[current]]}, fields='id').execute()['id']
                    except Exception:
                        failed_folders.add(next_path)
                        raise
                current = next_path
            parent_id = folders[parent_name]
        media = MediaFileUpload(str(path), chunksize=chunk_size, resumable=True)
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace('+00:00', 'Z')
        if name in remote:
            request = drive.api.files().update(fileId=remote[name]['id'], body={'modifiedTime':modified}, media_body=media, fields='id,md5Checksum,size,modifiedTime')
        else:
            request = drive.api.files().create(body={'name': path.name, 'parents': [parent_id], 'modifiedTime':modified}, media_body=media, fields='id,md5Checksum,size,modifiedTime')
        response = None
        while response is None:
            if cancel.is_set():
                raise InterruptedError('Cancelado')
            status, response = request.next_chunk(num_retries=3)
            if status:
                progress(status.resumable_progress)
        progress(size)
        if response.get('md5Checksum') != local[name]['md5Checksum']:
            raise RuntimeError(f'Arquivo mudou durante o envio: {name}. Execute novamente.')
        return local[name], response
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.drivebridge-' + uuid.uuid4().hex + '.part')
    before = digest(path, cancel) if path.exists() else None
    try:
        with temp.open('wb') as stream:
            if size:
                downloader = MediaIoBaseDownload(stream, drive.api.files().get_media(fileId=remote[name]['id']), chunksize=chunk_size)
                done = False
                while not done:
                    if cancel.is_set():
                        raise InterruptedError('Cancelado')
                    status, done = downloader.next_chunk(num_retries=3)
                    if status:
                        progress(status.resumable_progress)
        progress(size)
        checksum = digest(temp, cancel)
        if checksum != remote[name].get('md5Checksum'):
            raise RuntimeError(f'Falha na verificação do download: {name}')
        if (digest(path, cancel) if path.exists() else None) != before:
            raise RuntimeError(f'Arquivo local mudou durante download: {name}')
        if cancel.is_set():
            raise InterruptedError('Cancelado')
        if remote[name].get('modifiedTime'):
            timestamp = datetime.fromisoformat(remote[name]['modifiedTime'].replace('Z', '+00:00')).timestamp()
            os.utime(temp, (timestamp, timestamp))
        temp.replace(path)
        return {'md5Checksum': checksum, 'size': size, 'modifiedTime': path.stat().st_mtime}, remote[name]
    finally:
        temp.unlink(missing_ok=True)
