"""Persistência local e sincronização pela API oficial do Google Drive."""
import hashlib
import errno
from collections import Counter
import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

SCOPES = ['https://www.googleapis.com/auth/drive']
FOLDER = 'application/vnd.google-apps.folder'
TRANSFER_CHUNK_SIZE = 16 * 1024 * 1024
TRANSFER_WORKERS = 4


def config_dir():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'drivebridge'


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)


def load_profiles():
    path = config_dir() / 'profiles.json'
    return json.loads(path.read_text()) if path.exists() else {}


def save_profiles(profiles):
    write_json(config_dir() / 'profiles.json', profiles)


def accounts():
    path = config_dir() / 'accounts.json'
    return json.loads(path.read_text()) if path.exists() else {}


def bundled_oauth_client_file():
    return Path(__file__).parent / 'resources' / 'oauth-client.json'


def oauth_client_file():
    """Usa configuração explícita, pessoal ou incluída na distribuição."""
    configured = os.environ.get('DRIVEBRIDGE_OAUTH_CLIENT')
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    for path in (config_dir() / 'oauth-client.json', bundled_oauth_client_file()):
        if path.is_file():
            return path
    return None


def read_oauth_client(path):
    data = json.loads(Path(path).read_text())
    client = data.get('installed')
    if not isinstance(client, dict) or not client.get('client_id') or not client.get('client_secret'):
        raise ValueError('Escolha o JSON OAuth de um cliente do tipo Aplicativo para computador, não um token ou conta de serviço.')
    if client.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or client.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise ValueError('O JSON deve usar os endpoints oficiais de autorização do Google.')
    return data


def import_oauth_client(path):
    data = read_oauth_client(path)
    destination = config_dir() / 'oauth-client.json'
    write_json(destination, data)
    return destination


def connection_error(error):
    """Traduz falhas de configuração sem expor a resposta inteira na interface."""
    try:
        payload = json.loads(getattr(error, 'content', b'{}'))
        details = payload.get('error', {})
        reasons = {item.get('reason') for item in details.get('errors', [])}
        message = details.get('message', '')
    except (ValueError, TypeError, AttributeError):
        reasons, message = set(), ''
    if 'accessNotConfigured' in reasons or ('Google Drive API' in message and 'disabled' in message):
        project = re.search(r'project[ =](\d+)', message)
        project_id = project.group(1) if project else None
        url = 'https://console.cloud.google.com/apis/library/drive.googleapis.com'
        if project_id:
            url += '?project=' + project_id
        return {
            'title': 'Ative a Google Drive API',
            'message': ('A Google Drive API está desativada' + (f' no projeto {project_id}' if project_id else ' no projeto OAuth') + '.\n\n'
                '1. Clique em Abrir configuração no Google Cloud.\n'
                '2. Confira o projeto selecionado e clique em Ativar.\n'
                '3. Aguarde alguns minutos e tente Entrar com Google novamente.\n\n'
                'Se a API já estiver ativa, confira se você está no mesmo projeto do cliente OAuth. Não é necessário baixar o JSON novamente.'),
            'url': url,
        }
    if isinstance(error, TimeoutError):
        return {'title': 'Autorização não concluída', 'message': 'O tempo para autorizar terminou. Clique novamente em Entrar com Google e conclua a autorização no navegador.', 'url': None}
    return {'title': 'Não foi possível conectar', 'message': 'O Google não concluiu a conexão. Confira o cliente OAuth e os usuários de teste no projeto.\n\nDetalhes: ' + str(error)[:2000], 'url': None}


def build_drive(credentials):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    return build('drive', 'v3', http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=30)))


def system_failure(error):
    """Falhas globais precisam parar, em vez de repetir em cada arquivo."""
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    if isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EDQUOT, errno.ENODEV, errno.EROFS, errno.ENETUNREACH, errno.ECONNRESET, errno.ECONNREFUSED):
        return True
    status = getattr(getattr(error, 'resp', None), 'status', None)
    if isinstance(status, int) and (status == 401 or status == 429 or status >= 500):
        return True
    try:
        details = json.loads(getattr(error, 'content', b'{}')).get('error', {})
        reasons = {item.get('reason') for item in details.get('errors', [])}
        if reasons & {'accessNotConfigured', 'storageQuotaExceeded', 'dailyLimitExceeded', 'rateLimitExceeded', 'userRateLimitExceeded'}:
            return True
    except (ValueError, TypeError, AttributeError):
        pass
    return type(error).__name__ in {'RefreshError', 'TransportError', 'ServerNotFoundError'}


def scan_local(root, emit, cancel, reserved):
    files, blocked, links = {}, set(), set()
    def warn(path, reason):
        relative = path.relative_to(root).as_posix()
        blocked.add(relative)
        emit('log', f'NÃO TRANSFERIDO: {relative} — {reason}')
    def walk_error(error):
        path = Path(error.filename) if error.filename else root
        if path == root or system_failure(error):
            raise error
        warn(path, str(error))
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        if cancel.is_set():
            raise InterruptedError('Cancelado')
        for name in list(dirs) + names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if re.fullmatch(r'\.drivebridge-[0-9a-f]{32}\.part', name):
                warn(path, 'temporário de transferência anterior; não será enviado')
                continue
            if reserved(relative):
                if name in dirs:
                    dirs.remove(name)
                continue
            if path.is_symlink():
                links.add(relative)
                warn(path, 'link simbólico local; não será seguido')
                if name in dirs:
                    dirs.remove(name)
                continue
            if name in dirs:
                continue
            try:
                if not path.is_file():
                    warn(path, 'arquivo especial ou removido durante a varredura')
                    continue
                emit('status', f'Verificando arquivo local: {relative}')
                before = path.stat()
                checksum = digest(path, cancel)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    warn(path, 'arquivo alterado durante a varredura; tente novamente')
                    continue
                files[relative] = {'size': after.st_size, 'md5Checksum': checksum, 'modifiedTime': after.st_mtime}
            except InterruptedError:
                raise
            except OSError as error:
                if system_failure(error):
                    raise
                warn(path, str(error))
    return files, blocked, links


def connect(client_file, email='', notify=None):
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    flow = InstalledAppFlow.from_client_config(read_oauth_client(client_file), SCOPES, autogenerate_code_verifier=True)
    options = {'access_type': 'offline', 'prompt': 'consent'}
    if email:
        options['login_hint'] = email.strip()
    if notify:
        notify('status', 'Autorize no navegador. Aguardando a confirmação do Google…')
    credentials = flow.run_local_server(port=0, timeout_seconds=180,
        authorization_prompt_message='', success_message='Autorização recebida. Você pode fechar esta aba e voltar ao DriveBridge.', **options)
    service = build_drive(credentials)
    user = service.about().get(fields='user(emailAddress,displayName)').execute()['user']
    identifier = hashlib.sha256(user['emailAddress'].encode()).hexdigest()[:24]
    write_json(config_dir() / 'tokens' / (identifier + '.json'), json.loads(credentials.to_json()))
    records = accounts()
    records[identifier] = user
    write_json(config_dir() / 'accounts.json', records)
    return identifier


class Drive:
    def __init__(self, account):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        path = config_dir() / 'tokens' / (account + '.json')
        credentials = Credentials.from_authorized_user_file(str(path), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(lambda **kwargs: Request()(**dict(kwargs, timeout=30)))
            write_json(path, json.loads(credentials.to_json()))
        self.api = build_drive(credentials)

    def children(self, parent, folders_only=False):
        query = f"'{parent.replace(chr(39), chr(92)+chr(39))}' in parents and trashed = false"
        if folders_only:
            query += f" and mimeType = '{FOLDER}'"
        result, token = [], None
        while True:
            page = self.api.files().list(q=query, pageToken=token, pageSize=1000,
                fields='nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime)').execute(num_retries=3)
            result.extend(page.get('files', []))
            token = page.get('nextPageToken')
            if not token:
                return result

    def scan(self, parent, cancel, prefix='', skipped=None, notify=None):
        skipped = skipped if skipped is not None else {}
        files, folders = {}, {'': parent}
        if notify:
            notify('status', f"Verificando Drive: {prefix or 'Meu Drive (root)'}")
        items = self.children(parent)
        counts = Counter(item['name'] for item in items)
        for item in items:
            if cancel.is_set():
                raise InterruptedError('Cancelado')
            name = item['name']
            relative = prefix + name
            if counts[name] > 1:
                skipped[relative] = 'nomes duplicados no Drive; renomeie os itens para resolver'
                continue
            if item['mimeType'].startswith('application/vnd.google-apps.') and item['mimeType'] != FOLDER:
                skipped[relative] = 'documento nativo ou atalho Google'
                continue
            if name in ('.', '..', '') or '/' in name or '\x00' in name or len(os.fsencode(name)) > 255:
                skipped[relative] = 'nome incompatível com o sistema de arquivos local'
                continue
            if item['mimeType'] == FOLDER:
                try:
                    subfiles, subfolders = self.scan(item['id'], cancel, relative + '/', skipped, notify)
                except InterruptedError:
                    raise
                except Exception as error:
                    if system_failure(error):
                        raise
                    skipped[relative] = f'pasta inacessível: {error}'
                    continue
                folders[relative] = item['id']
                files.update(subfiles)
                folders.update({k: v for k, v in subfolders.items() if k})
            elif not item.get('md5Checksum') or 'size' not in item:
                skipped[relative] = 'arquivo sem metadados necessários para verificação'
            else:
                files[relative] = item
        return files, folders


def digest(path, cancel=None):
    result = hashlib.md5()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            if cancel is not None and cancel.is_set():
                raise InterruptedError('Cancelado')
            result.update(block)
    return result.hexdigest()


def plan(local, remote, baseline, direction, overwrite='changed'):
    def modified(item):
        value = item.get('modifiedTime')
        if isinstance(value, (float, int)):
            return value
        if isinstance(value, str):
            from datetime import datetime
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
            except ValueError:
                pass
        return None
    if overwrite not in ('changed', 'always', 'newer'):
        raise ValueError('Opção de sobrescrita inválida.')
    tasks, conflicts = [], []
    for name in sorted(set(local) | set(remote)):
        left, right = local.get(name), remote.get(name)
        lh = left['md5Checksum'] if left else None
        rh = right.get('md5Checksum') if right else None
        if left and right and lh == rh and not (overwrite == 'always' and direction != 'both'):
            continue
        if overwrite == 'newer' and left and right:
            lt, rt = modified(left), modified(right)
            if lt is None or rt is None:
                conflicts.append(name)
                continue
            if direction == 'upload' and lt <= rt or direction == 'download' and rt <= lt:
                continue
            if direction == 'both':
                if lt == rt:
                    conflicts.append(name)
                elif lt > rt:
                    tasks.append(('upload', name, left['size']))
                else:
                    tasks.append(('download', name, int(right['size'])))
                continue
        if direction == 'upload':
            if left:
                tasks.append(('upload', name, left['size']))
        elif direction == 'download':
            if right:
                tasks.append(('download', name, int(right['size'])))
        elif direction == 'both':
            old = baseline.get(name)
            if left and right:
                if old and lh == old.get('local'):
                    tasks.append(('download', name, int(right['size'])))
                elif old and rh == old.get('remote'):
                    tasks.append(('upload', name, left['size']))
                else:
                    conflicts.append(name)
            elif left:
                tasks.append(('upload', name, left['size']))
            elif right:
                tasks.append(('download', name, int(right['size'])))
        else:
            raise ValueError('Direção inválida')
    return tasks, conflicts


def synchronize(profile, emit, cancel):
    workers = int(profile.get('workers', TRANSFER_WORKERS))
    chunk_size = int(profile.get('chunk_mib', 16)) * 1024 * 1024
    if not 1 <= workers <= 8 or chunk_size not in [value * 1024 * 1024 for value in (4, 8, 16, 32)]:
        raise ValueError('Use de 1 a 8 transferências e blocos de 4, 8, 16 ou 32 MiB.')
    root = Path(profile['local']).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('A pasta local não existe.')
    emit('phase', 'scanning')
    emit('status', 'Verificando arquivos e calculando o total…')
    drive = Drive(profile['account'])
    skipped = {}
    remote, folders = drive.scan(profile['remote'], cancel, skipped=skipped, notify=emit)
    emit('log', f'Varredura remota: {len(remote)} arquivos comuns, {len(folders)} pastas, {len(skipped)} itens nativos/atalhos.')
    for name in sorted(skipped):
        emit('log', f'NÃO TRANSFERIDO: {name} — {skipped[name]}; permanece no Drive.')
    def reserved(name):
        return any(name == excluded or name.startswith(excluded + '/') for excluded in skipped)
    # Evita criar arquivos ou pastas com o mesmo nome de itens não transferidos.
    remote = {name: item for name, item in remote.items() if not reserved(name)}
    local, blocked_local, skipped_links = scan_local(root, emit, cancel, reserved)
    for name in list(remote):
        if any(excluded == '.' or name == excluded or name.startswith(excluded + '/') for excluded in blocked_local):
            emit('log', f'NÃO TRANSFERIDO: {name} — caminho local inacessível, especial ou link simbólico.')
            del remote[name]
    identity = hashlib.sha256(json.dumps([profile['account'], str(root), profile['remote']]).encode()).hexdigest()
    state_path = config_dir() / 'states' / (identity + '.json')
    baseline = json.loads(state_path.read_text()) if state_path.exists() else {}
    path_conflicts = set()
    for name in list(remote):
        destination = root / name
        if (destination.exists() and not destination.is_file()) or any(parent.is_file() or parent.is_symlink() for parent in destination.parents if parent != root and root in parent.parents):
            path_conflicts.add(name)
            del remote[name]
    for name in list(local):
        if name in folders:
            path_conflicts.add(name)
            del local[name]
    # Impede uploads sob um arquivo remoto e vice-versa.
    for name in list(local):
        if any(parent.as_posix() in remote for parent in Path(name).parents if parent.as_posix() != '.'):
            path_conflicts.add(name)
            del local[name]
    tasks, conflicts = plan(local, remote, baseline, profile['direction'], profile.get('overwrite', 'changed'))
    conflicts = sorted(set(conflicts) | path_conflicts)
    for name in conflicts:
        emit('log', f'CONFLITO: {name} — escolha uma direção para resolver conscientemente.')
    emit('log', f'Plano: {len(tasks)} transferências, {len(conflicts)} conflitos. Origem remota: {profile["remote"]}.')
    total = sum(size for _, _, size in tasks)
    from .transfers import transfer_file
    completed = 0
    confirmed_bytes = 0
    succeeded = 0
    finished_count = 0
    failures = []
    active = {}
    metrics_lock = threading.Lock()
    folder_lock = threading.Lock()
    connection_lock = threading.Lock()
    failed_folders = set()
    connections = threading.local()
    emit('phase', 'transferring')
    emit('status', f'Transferindo com até {workers} arquivos em paralelo…')
    emit('progress', (0, total, '', 0, len(tasks), time.monotonic(), '', 0, ()))

    def report(name='', action=''):
        # Chamado apenas com metrics_lock adquirido, para manter ordem e totais.
        in_flight = sum(value[0] for value in active.values())
        names = tuple((key, value[1]) for key, value in active.items())
        emit('progress', (completed + in_flight, total, name, finished_count,
                          len(tasks), time.monotonic(), action, confirmed_bytes, names))

    def run_task(task):
        nonlocal confirmed_bytes
        action, name, size = task
        if cancel.is_set():
            raise InterruptedError('Cancelado')
        with metrics_lock:
            active[name] = (0, action)
            report(name, action)
        emit('log', f'{action}: {name}')
        def progress(value):
            nonlocal confirmed_bytes
            with metrics_lock:
                previous = active[name][0]
                amount = max(previous, min(size, value))
                confirmed_bytes += amount - previous
                active[name] = (amount, action)
                report(name, action)
        if not hasattr(connections, 'drive'):
            # A atualização do token gravado também fica serializada.
            with connection_lock:
                connections.drive = Drive(profile['account'])
        return transfer_file(connections.drive, task, root, local, remote, folders,
                             folder_lock, failed_folders, progress, cancel, chunk_size)

    fatal = None
    remaining = iter(tasks)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='drivebridge-transfer') as executor:
        pending = {}
        def submit_next():
            if cancel.is_set() or fatal is not None:
                return
            task = next(remaining, None)
            if task is not None:
                pending[executor.submit(run_task, task)] = task
        for _ in range(workers):
            submit_next()
        while pending:
            done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for future in done:
                action, name, size = pending.pop(future)
                success = False
                transferred = False
                try:
                    left, right = future.result()
                    transferred = True
                    local[name], remote[name] = left, right
                    baseline[name] = {'local': left['md5Checksum'], 'remote': right.get('md5Checksum')}
                    write_json(state_path, baseline)
                    success = True
                except InterruptedError:
                    cancel.set()
                except Exception as error:
                    if system_failure(error) or transferred:
                        fatal = fatal or error
                        cancel.set()
                    else:
                        failures.append(name)
                        emit('log', f'PENDENTE: {name} — {error}')
                with metrics_lock:
                    active.pop(name, None)
                    finished_count += 1
                    if success:
                        completed += size
                        succeeded += 1
                    report(name, action)
                submit_next()
    if fatal is not None:
        raise fatal
    if cancel.is_set():
        raise InterruptedError('Cancelado')
    for name in set(local) & set(remote):
        if local[name]['md5Checksum'] == remote[name].get('md5Checksum'):
            baseline[name] = {'local': local[name]['md5Checksum'], 'remote': remote[name]['md5Checksum']}
    write_json(state_path, baseline)
    emit('status', f'Concluído: {succeeded} transferências; {len(failures)} falhas pendentes; {len(conflicts)} conflitos; {len(skipped)} itens remotos não transferidos; {len(blocked_local)} itens locais não transferidos, incluindo {len(skipped_links)} links locais. Consulte a atividade.')
