"""Página interna do DriveBridge: um processo, um sincronizador e um agendador."""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime
from datetime import timedelta
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .core import (Drive, SCOPES, accounts, build_drive, config_dir, connection_error,
                   import_oauth_client, load_profiles, oauth_client_file, read_oauth_client,
                   save_profiles, synchronize, write_json)
from .notifications import Notifier, public_settings, validate_settings
from .scheduling import ZONE
from .progress import TransferStats, format_bytes
from .scheduling import normalize_schedule, next_run, FREQUENCIES


class SyncManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.busy = False
        self.phase = None
        self.status = 'Pronto para sincronizar.'
        self.progress = None
        self.active_profile = None
        self.stats = TransferStats()
        self.stats.active = False
        self.logs = deque(maxlen=1000)
        self.sequence = 0
        self.due = {}
        self.last_runs = {}
        self.schedule_records_path = config_dir() / 'schedule-state.json'
        try:
            self.schedule_records = json.loads(self.schedule_records_path.read_text())
        except (OSError, ValueError):
            self.schedule_records = {}
        self.closed = threading.Event()
        self.logger = logging.getLogger('drivebridge')
        self.operation_log = None
        self.notifier = Notifier(self.emit)

    def emit(self, kind, value):
        with self.lock:
            if kind == 'status':
                self.status = value
                if value.startswith('Concluído:'):
                    self.emit('log', value)
            elif kind == 'phase':
                self.phase = value
                self.progress = None
                self.stats.reset(time.monotonic())
                self.stats.active = value == 'transferring'
            elif kind == 'progress':
                self.progress = value
                if value[3] == 0 and not value[2]:
                    self.stats.reset(value[5])
                self.stats.update(value[7] if len(value) > 7 else value[0], value[1], value[5])
            elif kind == 'log':
                self.sequence += 1
                self.logs.append({'id': self.sequence, 'time': time.time(), 'message': value})
            if kind in ('log', 'status'):
                if self.operation_log is not None:
                    self.operation_log.write(datetime.now(ZONE).isoformat(timespec='seconds') + '  ' + str(value) + '\n')
                    self.operation_log.flush()
                self.logger.info('%s: %s', kind, value)

    def start(self, identifier):
        with self.lock:
            if self.busy:
                raise ValueError('Já existe uma operação em andamento. Aguarde ou cancele.')
            profiles = load_profiles()
            if identifier not in profiles:
                raise ValueError('Par de pastas não encontrado.')
            profile = profiles[identifier].copy()
            if profile['account'] not in accounts():
                raise ValueError('A conta do par não está conectada.')
            report_dir = config_dir() / 'reports'
            report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            log_path = report_dir / (datetime.now(ZONE).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex + '.log')
            fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.operation_log = os.fdopen(fd, 'w')
            started = time.time()
            self.busy = True
            self.cancel.clear()
            self.phase = 'scanning'
            self.progress = None
            self.active_profile = identifier
            self.status = 'Iniciando sincronização…'
            self.stats.reset(time.monotonic())
            self.stats.active = False
            self.emit('log', f'Iniciando: {profile["name"]} • remoto: {profile["remote"]}')
            try:
                self.set_due(identifier, profile)
            except Exception:
                self.operation_log.close()
                self.operation_log = None
                self.busy = False
                raise
        def run():
            outcome = 'Concluída'
            try:
                synchronize(profile, self.emit, self.cancel)
            except InterruptedError:
                outcome = 'Cancelada'
                self.emit('status', 'Operação cancelada. Arquivos concluídos foram preservados.')
                self.emit('log', self.status)
            except Exception as error:
                outcome = 'Falha'
                self.logger.exception('Falha na sincronização web')
                message = connection_error(error)['message'] if getattr(error, 'content', None) else str(error)
                self.emit('status', 'Operação interrompida: ' + message)
                self.emit('log', 'ERRO: ' + message)
            finally:
                with self.lock:
                    self.busy = False
                    self.stats.active = False
                    self.stats.finished = time.monotonic()
                    self.phase = None
                    ended = time.time()
                    result = self.status
                    snapshot = self.snapshot()['progress']
                    self.operation_log.close()
                    self.operation_log = None
                    self.last_runs[identifier] = {'time': ended, 'status': result}
                body = ('DriveBridge — resultado da sincronização\n\n'
                    f'Par: {profile["name"]}\nResultado: {outcome}\n'
                    f'Início: {datetime.fromtimestamp(started, ZONE).isoformat(timespec="seconds")}\n'
                    f'Término: {datetime.fromtimestamp(ended, ZONE).isoformat(timespec="seconds")}\n'
                    f'Duração: {ended - started:.1f} segundos\n'
                    f'Direção: { {"upload":"Local → Drive", "download":"Drive → Local", "both":"Bidirecional"}[profile["direction"]] }\n'
                    f'Pasta local: {profile["local"]}\nPasta remota: {profile["remote"]}\n'
                    f'Bytes concluídos: {snapshot["done_text"]} / {snapshot["total_text"]}\n'
                    f'Arquivos processados: {snapshot["finished"]} / {snapshot["files"]}\n'
                    f'Taxa média da etapa de transferência: {snapshot["average"]}\n\n'
                    f'{result}\n\nO log completo desta operação segue no anexo compactado (.gz).')
                try:
                    self.notifier.enqueue(f'DriveBridge — {outcome} — {profile["name"]}', body, log_path, recipient=accounts().get(profile['account'], {}).get('emailAddress'))
                except Exception:
                    self.emit('log', 'E-mail: não foi possível colocar o relatório na fila. O log permanece salvo no servidor.')
        threading.Thread(target=run, name='drivebridge-sync', daemon=True).start()

    def set_due(self, key, profile, initial=False):
        signature = normalize_schedule(profile)
        record = self.schedule_records.get(key, {})
        # Horários futuros sobrevivem a reinícios; após iniciar executa em cada inicialização.
        use_saved = initial and signature['start'] != 'startup' and record.get('schedule') == signature
        due = record.get('due') if use_saved else next_run(profile, initial=initial)
        if due is None:
            self.due.pop(key, None)
            self.schedule_records.pop(key, None)
        else:
            self.due[key] = time.monotonic() + max(0, due - time.time())
            self.schedule_records[key] = {'schedule': signature, 'due': due}
        write_json(self.schedule_records_path, self.schedule_records)

    def schedule_once(self):
        with self.lock:
            profiles = load_profiles()
            for key in list(self.due):
                if key not in profiles or not profiles[key].get('enabled'):
                    self.due.pop(key, None)
            for key, profile in profiles.items():
                if profile.get('enabled') and normalize_schedule(profile)['frequency'] != 'manual':
                    if key not in self.due:
                        self.set_due(key, profile, initial=True)
                    if not self.busy and time.monotonic() >= self.due[key]:
                        try:
                            self.start(key)
                        except (ValueError, OSError) as error:
                            self.set_due(key, profile)
                            self.emit('log', f'Agendamento pendente: {profile["name"]} — {error}')
                        break

    def scheduler(self):
        while not self.closed.wait(1):
            try:
                self.schedule_once()
            except Exception:
                self.logger.exception('Falha na leitura do agendamento')

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            rate, average = self.stats.rates(now)
            progress = self.progress
            value = {'done': 0, 'total': 0, 'finished': 0, 'files': 0, 'percent': 0, 'active': []}
            if progress:
                done, total, name, count, number = progress[:5]
                percent = done / total * 100 if total else (100 if count == number else 0)
                if count < number:
                    percent = min(percent, 99.9)
                value.update(done=done, total=total, finished=count, files=number, percent=percent,
                    active=[{'name': name, 'direction': action} for name, action in progress[8]] if len(progress) > 8 else [])
            value.update(done_text=format_bytes(value['done']), total_text=format_bytes(value['total']),
                         rate=format_bytes(rate) + '/s', average=format_bytes(average) + '/s')
            return {'busy': self.busy, 'phase': self.phase, 'status': self.status, 'progress': value,
                    'active_profile': self.active_profile, 'logs': list(self.logs), 'last_runs': self.last_runs.copy(),
                    'next_runs': {key: time.time() + max(0, due - now) for key, due in self.due.items()}}


def web_client_config(data):
    client = data.get('installed') or data.get('web')
    if not isinstance(client, dict) or not client.get('client_id') or not client.get('client_secret'):
        raise ValueError('Importe o JSON OAuth do aplicativo (desktop ou web).')
    if client.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or client.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise ValueError('Use somente o JSON OAuth oficial do Google.')
    return data


def web_oauth_client_file():
    path = config_dir() / 'oauth-web-client.json'
    return path if path.is_file() else oauth_client_file()


def create_app(manager=None, start_scheduler=False):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 128 * 1024
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings_path = folder / 'web-settings.json'
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    else:
        password = os.environ.get('DRIVEBRIDGE_WEB_PASSWORD') or secrets.token_urlsafe(24)
        settings = {'secret': secrets.token_hex(32), 'password_hash': generate_password_hash(password)}
        write_json(settings_path, settings)
        if not os.environ.get('DRIVEBRIDGE_WEB_PASSWORD'):
            fd = os.open(folder / 'web-password.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(password + '\n')
    public_url = os.environ.get('DRIVEBRIDGE_PUBLIC_URL', 'http://localhost:8765').rstrip('/')
    public_host = urlparse(public_url).hostname
    allowed = {'localhost', '127.0.0.1', public_host}
    allowed.update(filter(None, os.environ.get('DRIVEBRIDGE_ALLOWED_HOSTS', '').split(',')))
    app.config.update(SECRET_KEY=settings['secret'], SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=public_url.startswith('https://'), PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    manager = manager or SyncManager()
    app.extensions['sync_manager'] = manager
    pending_oauth = {}
    oauth_lock = threading.Lock()

    @app.before_request
    def guard():
        if urlparse('http://' + request.host).hostname not in allowed:
            return jsonify(error='Host não autorizado.'), 400
        if request.endpoint == 'static':
            return None
        if request.method in ('POST', 'PUT', 'DELETE'):
            expected = session.get('csrf', '')
            submitted = request.headers.get('X-CSRF-Token') or request.form.get('csrf', '')
            if not expected or not hmac.compare_digest(expected, submitted):
                return jsonify(error='Sessão inválida. Atualize a página.'), 403
        if not session.get('authenticated') and request.endpoint not in ('login',):
            if request.path.startswith('/api/'):
                return jsonify(error='Faça login na página interna.'), 401
            return redirect('/login')

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(OSError)
    def storage_error(error):
        app.logger.exception('Falha local')
        return jsonify(error='Não foi possível acessar o caminho informado: ' + str(error)), 400

    @app.get('/api/email')
    def email_configuration():
        return jsonify(public_settings())

    @app.post('/api/email')
    def save_email_configuration():
        with manager.notifier.lock:
            settings = validate_settings(request.get_json() or {})
            write_json(config_dir() / 'email-settings.json', settings)
        return jsonify(public_settings())

    @app.post('/api/email/retry')
    def retry_email_reports():
        return jsonify(count=manager.notifier.retry_failed())

    @app.post('/api/email/test')
    def test_email_configuration():
        account = (request.get_json() or {}).get('account')
        recipient = accounts().get(account, {}).get('emailAddress')
        manager.notifier.enqueue('DriveBridge — teste de envio', 'Configuração de e-mail do DriveBridge: mensagem de teste. Nenhuma sincronização foi iniciada.', test=True, recipient=recipient)
        return jsonify(ok=True)

    @app.get('/')
    def index():
        return render_template('web.html', csrf=session['csrf'])

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        session.setdefault('csrf', secrets.token_urlsafe(32))
        error = None
        if request.method == 'POST':
            if check_password_hash(settings['password_hash'], request.form.get('password', '')):
                session.clear()
                session.permanent = True
                session['authenticated'] = True
                session['csrf'] = secrets.token_urlsafe(32)
                return redirect('/')
            error = 'Senha incorreta.'
        return render_template('login.html', csrf=session['csrf'], error=error)

    @app.post('/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/state')
    def state():
        result = manager.snapshot()
        result.update(accounts=accounts(), profiles=load_profiles(), oauth_configured=bool(web_oauth_client_file()),
                      server_home=str(Path.home()), public_url=public_url)
        return jsonify(result)

    @app.post('/api/profiles')
    def save_profile():
        data = request.get_json() or {}
        if manager.busy:
            raise ValueError('Aguarde ou cancele a sincronização antes de alterar pares.')
        with manager.lock:
            if manager.busy:
                raise ValueError('Aguarde ou cancele a sincronização antes de alterar pares.')
            identifier = data.get('id') or uuid.uuid4().hex
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', identifier):
                raise ValueError('Identificador inválido.')
            if data.get('account') not in accounts():
                raise ValueError('Selecione uma conta conectada.')
            if data.get('direction') not in ('upload', 'download', 'both'):
                raise ValueError('Selecione uma direção válida.')
            interval = int(data.get('interval', 15))
            if interval < 1 or interval > 525600:
                raise ValueError('Intervalo: entre 1 minuto e 365 dias.')
            local = Path(data.get('local', '')).expanduser()
            if not data.get('local') or not local.is_absolute() or not local.is_dir():
                raise ValueError('Escolha uma pasta existente no servidor Debian.')
            name, remote = str(data.get('name', '')).strip(), str(data.get('remote', '')).strip()
            if not name or not remote:
                raise ValueError('Informe nome e pasta remota.')
            workers = int(data.get('workers', 4))
            chunk_mib = int(data.get('chunk_mib', 16))
            if not 1 <= workers <= 8 or chunk_mib not in (4, 8, 16, 32):
                raise ValueError('Concorrência: 1 a 8; blocos: 4, 8, 16 ou 32 MiB.')
            profile = dict(workers=workers, chunk_mib=chunk_mib, id=identifier, name=name, account=data['account'], direction=data['direction'],
                           local=str(local), remote=remote, enabled=bool(data.get('enabled')), interval=interval)
            schedule = normalize_schedule(data)
            if data.get('schedule') is not None:
                profile['schedule'] = schedule
                profile['enabled'] = schedule['frequency'] != 'manual'
                profile['interval'] = schedule['every'] * FREQUENCIES[schedule['frequency']]
            overwrite = data.get('overwrite', 'changed')
            if overwrite not in ('changed', 'always', 'newer'):
                raise ValueError('Escolha uma opção de sobrescrita válida.')
            if overwrite == 'always' and profile['direction'] == 'both':
                raise ValueError('Sobrescrever todos exige escolher uma direção: Local → Drive ou Drive → Local.')
            profile['overwrite'] = overwrite
            profiles = load_profiles()
            profiles[identifier] = profile
            save_profiles(profiles)
            manager.due.pop(identifier, None)
            manager.schedule_records.pop(identifier, None)
            write_json(manager.schedule_records_path, manager.schedule_records)
            return jsonify(profile)

    @app.delete('/api/profiles/<identifier>')
    def remove_profile(identifier):
        with manager.lock:
            if manager.busy:
                raise ValueError('Aguarde ou cancele a operação antes de remover pares.')
            profiles = load_profiles()
            profiles.pop(identifier, None)
            save_profiles(profiles)
            manager.due.pop(identifier, None)
        return jsonify(ok=True)

    @app.post('/api/sync/<identifier>')
    def start_sync(identifier):
        manager.start(identifier)
        return jsonify(ok=True)

    @app.post('/api/cancel')
    def cancel_sync():
        manager.cancel.set()
        manager.emit('status', 'Cancelando… aguardando as chamadas em andamento.')
        return jsonify(ok=True)

    @app.get('/api/folders/local')
    def local_folders():
        path = Path(request.args.get('path') or Path.home()).expanduser().resolve()
        notice = ''
        if not path.is_dir():
            if request.args.get('fallback') != 'home':
                raise ValueError('Pasta não encontrada no servidor. Escolha Sistema / ou Pasta pessoal para navegar.')
            path = Path.home().resolve()
            notice = 'O caminho anterior não existe no Debian. Escolha uma pasta deste servidor.'
        children = []
        for child in path.iterdir():
            try:
                if child.is_dir():
                    children.append({'name': child.name, 'id': str(child)})
            except OSError:
                continue
        return jsonify(path=str(path), parent=str(path.parent), notice=notice, folders=sorted(children, key=lambda x: x['name'].lower()))

    @app.get('/api/folders/remote')
    def remote_folders():
        account = request.args.get('account', '')
        if account not in accounts():
            raise ValueError('Escolha uma conta conectada.')
        parent = request.args.get('parent', 'root')
        items = Drive(account).children(parent, True)
        return jsonify(folders=sorted([{'id': item['id'], 'name': item['name']} for item in items], key=lambda x: x['name'].lower()))

    @app.post('/api/oauth/config')
    def oauth_config():
        if 'file' not in request.files:
            raise ValueError('Escolha o JSON OAuth.')
        data = web_client_config(json.load(request.files['file']))
        write_json(folder / ('oauth-web-client.json' if 'web' in data else 'oauth-client.json'), data)
        return jsonify(ok=True)

    @app.post('/api/oauth/start')
    def oauth_start():
        from google_auth_oauthlib.flow import Flow
        path = web_oauth_client_file()
        if not path:
            raise ValueError('Configure o cliente OAuth uma vez nas opções avançadas.')
        email = str((request.get_json() or {}).get('email', '')).strip()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise ValueError('Informe um e-mail válido.')
        data = web_client_config(json.loads(path.read_text()))
        if 'installed' in data and public_host not in ('localhost', '127.0.0.1'):
            raise ValueError('Cliente desktop exige acesso por localhost via túnel SSH. Configure um cliente OAuth web e HTTPS para usar outro endereço.')
        if request.host != urlparse(public_url).netloc:
            raise ValueError('Para autorizar uma nova conta, abra a página pelo endereço ' + public_url + ' (túnel SSH se o servidor for remoto). As contas já conectadas podem sincronizar pelo endereço interno.')
        flow = Flow.from_client_config(data, scopes=SCOPES, redirect_uri=public_url + '/oauth/callback', autogenerate_code_verifier=True)
        # Apenas loopback permite HTTP; os tokens sempre usam HTTPS com Google.
        if public_host in ('localhost', '127.0.0.1') and public_url.startswith('http://'):
            os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        elif not public_url.startswith('https://'):
            raise ValueError('OAuth web fora do localhost exige HTTPS.')
        url, oauth_state = flow.authorization_url(access_type='offline', prompt='consent', login_hint=email)
        with oauth_lock:
            now = time.monotonic()
            for old in list(pending_oauth):
                if pending_oauth[old]['expires'] < now:
                    pending_oauth.pop(old)
            pending_oauth[oauth_state] = {'flow': flow, 'expires': now + 180, 'owner': session['csrf']}
        return jsonify(url=url)

    @app.get('/oauth/callback')
    def oauth_callback():
        oauth_state = request.args.get('state', '')
        with oauth_lock:
            record = pending_oauth.pop(oauth_state, None)
        if not record or record['expires'] < time.monotonic() or not hmac.compare_digest(record['owner'], session.get('csrf', '')):
            raise ValueError('Autorização expirada ou inválida. Tente novamente.')
        if request.args.get('error'):
            manager.emit('log', 'Autorização Google não concluída: ' + request.args['error'])
            return redirect('/')
        try:
            flow = record['flow']
            flow.fetch_token(authorization_response=public_url + '/oauth/callback?' + request.query_string.decode())
            user = build_drive(flow.credentials).about().get(fields='user(emailAddress,displayName)').execute(num_retries=3)['user']
            identifier = hashlib.sha256(user['emailAddress'].encode()).hexdigest()[:24]
            with manager.lock:
                write_json(folder / 'tokens' / (identifier + '.json'), json.loads(flow.credentials.to_json()))
                records = accounts()
                records[identifier] = user
                write_json(folder / 'accounts.json', records)
            manager.emit('log', 'Conta Google conectada: ' + user['emailAddress'])
        except Exception as error:
            manager.emit('status', connection_error(error)['message'])
            manager.emit('log', 'Conexão Google pendente: ' + connection_error(error)['message'])
        return redirect('/')

    if start_scheduler:
        manager.notifier.start()
        threading.Thread(target=manager.scheduler, name='drivebridge-schedule', daemon=True).start()
    return app


def main():
    from logging.handlers import RotatingFileHandler
    from waitress import create_server
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / 'activity.log'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)
    handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger = logging.getLogger('drivebridge')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    import fcntl
    lock_file = (folder / 'web-instance.lock').open('a')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Uma instância web do DriveBridge já está em execução.')
    app = create_app(start_scheduler=True)
    server = create_server(app, host=os.environ.get('DRIVEBRIDGE_BIND', '127.0.0.1'), port=int(os.environ.get('DRIVEBRIDGE_PORT', '8765')), threads=8)
    import signal
    manager = app.extensions['sync_manager']
    def stop(signum=None, frame=None):
        manager.closed.set()
        manager.cancel.set()
        def drain():
            deadline = time.monotonic() + 150
            while manager.busy and time.monotonic() < deadline:
                time.sleep(0.2)
            server.close()
        threading.Thread(target=drain, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.run()
    finally:
        manager.closed.set()
        manager.cancel.set()


if __name__ == '__main__':
    main()
