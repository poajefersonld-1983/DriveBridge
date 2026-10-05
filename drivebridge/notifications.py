"""Relatórios SMTP em segundo plano, com fila persistente e TLS obrigatório."""
import gzip
import json
import logging
import re
import shutil
import smtplib
import ssl
import threading
import time
import uuid
from email.message import EmailMessage
from pathlib import Path

from .core import config_dir, write_json

SETTINGS = 'email-settings.json'


def read_settings():
    path = config_dir() / SETTINGS
    return json.loads(path.read_text()) if path.exists() else {'enabled': False, 'host': '', 'port': 587, 'security': 'starttls', 'username': '', 'sender': '', 'recipients': []}


def public_settings():
    settings = read_settings()
    return {**{key: value for key, value in settings.items() if key != 'password'}, 'password_saved': bool(settings.get('password'))}


def validate_settings(data):
    previous = read_settings()
    settings = dict(enabled=bool(data.get('enabled')), host=str(data.get('host', '')).strip(), port=int(data.get('port', 587)), security=data.get('security', 'starttls'), username=str(data.get('username', '')).strip(), sender=str(data.get('sender', '')).strip(), recipients=data.get('recipients', []))
    if settings['security'] not in ('starttls', 'ssl') or not 1 <= settings['port'] <= 65535:
        raise ValueError('Escolha TLS na porta 587 ou SSL/TLS na porta 465, conforme seu provedor.')
    recipients = settings['recipients']
    if isinstance(recipients, str):
        recipients = [value.strip() for value in re.split('[,;]', recipients) if value.strip()]
    if not isinstance(recipients, list) or any(not isinstance(value, str) for value in recipients):
        raise ValueError('Informe os destinatários separados por vírgulas.')
    settings['recipients'] = recipients
    for value in [settings['sender'], *recipients]:
        if value and not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', value):
            raise ValueError('Informe endereços de e-mail válidos, sem nomes adicionais.')
    if any(char in settings['host'] for char in '\r\n/ '):
        raise ValueError('Informe somente o nome ou IP do servidor SMTP.')
    settings['password'] = str(data.get('password') or previous.get('password', ''))
    if settings['enabled'] and (not settings['host'] or not settings['sender']):
        raise ValueError('Informe servidor SMTP, remetente e destinatário para ativar.')
    if settings['enabled'] and settings['username'] and not settings['password']:
        raise ValueError('Informe a senha SMTP ou senha de aplicativo do provedor.')
    return settings


def send_message(settings, subject, body, log_path=None):
    message = EmailMessage()
    message['Subject'] = subject.replace('\r', ' ').replace('\n', ' ')[:200]
    message['From'] = settings['sender']
    message['To'] = ', '.join(settings['recipients'])
    message.set_content(body)
    if log_path:
        compressed = Path(log_path).with_suffix('.log.gz')
        with Path(log_path).open('rb') as source, gzip.open(compressed, 'wb') as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
        compressed.chmod(0o600)
        try:
            if compressed.stat().st_size > 20 * 1024 * 1024:
                raise ValueError('Log compactado maior que 20 MiB. Relatório preservado no servidor; envio não concluído.')
            message.add_attachment(compressed.read_bytes(), maintype='application', subtype='gzip', filename='drivebridge-log.txt.gz')
        finally:
            compressed.unlink(missing_ok=True)
    context = ssl.create_default_context()
    factory = smtplib.SMTP_SSL if settings['security'] == 'ssl' else smtplib.SMTP
    options = {'host': settings['host'], 'port': settings['port'], 'timeout': 30}
    if settings['security'] == 'ssl':
        options['context'] = context
    with factory(**options) as smtp:
        smtp.ehlo()
        if settings['security'] == 'starttls':
            smtp.starttls(context=context)
            smtp.ehlo()
        if settings['username']:
            smtp.login(settings['username'], settings.get('password', ''))
        refused = smtp.send_message(message)
        if refused:
            raise RuntimeError('O servidor SMTP recusou um ou mais destinatários. Confira os endereços.')


class Notifier:
    def __init__(self, emit):
        self.emit = emit
        self.closed = threading.Event()
        self.folder = config_dir() / 'email-outbox'
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.worker = None
        self.lock = threading.Lock()

    def enqueue(self, subject, body, log_path=None, test=False, recipient=None):
        settings = read_settings()
        if not settings.get('enabled'):
            if test:
                raise ValueError('Ative e salve a configuração de e-mail antes de testar.')
            return
        if not recipient or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', recipient):
            raise ValueError('A conta conectada não possui um e-mail válido para o relatório.')
        identifier = uuid.uuid4().hex
        with self.lock:
            write_json(self.folder / (identifier + '.json'), {'subject':subject,'body':body,'log_path':str(log_path) if log_path else None,'attempts':0,'due':time.time(),'state':'pending','recipient':recipient})
        self.emit('log', 'E-mail: relatório colocado na fila de envio.' if not test else 'E-mail: mensagem de teste colocada na fila.')

    def retry_failed(self):
        count = 0
        with self.lock:
            for path in self.folder.glob('*.json'):
                job = json.loads(path.read_text())
                if job['state'] == 'failed':
                    job.update(state='pending', attempts=0, due=time.time())
                    write_json(path, job)
                    count += 1
        return count

    def process_once(self):
        settings = read_settings()
        if not settings.get('enabled'):
            return
        with self.lock:
            paths = sorted(self.folder.glob('*.json'))
        for path in paths:
            job = json.loads(path.read_text())
            if job['state'] != 'pending' or job['due'] > time.time():
                continue
            try:
                send_message({**settings, 'recipients':[job['recipient']]}, job['subject'], job['body'], job['log_path'])
                job.update(state='sent', sent=time.time())
                self.emit('log', 'E-mail: relatório aceito pelo servidor SMTP.')
            except Exception:
                # Não exibe respostas SMTP que possam conter credenciais ou o conteúdo do relatório.
                logging.getLogger('drivebridge').warning('Falha no envio SMTP do relatório; tentativa %s.', job['attempts'] + 1)
                job['attempts'] += 1
                if job['attempts'] >= 4:
                    job.update(state='failed')
                    self.emit('log', 'E-mail: envio falhou após 4 tentativas. Relatório preservado; confira a configuração SMTP.')
                else:
                    job['due'] = time.time() + (60, 300, 900)[job['attempts'] - 1]
                    self.emit('log', 'E-mail: falha no envio; nova tentativa agendada. A sincronização foi preservada.')
            write_json(path, job)
            return  # Uma mensagem por vez, sem competir com a sincronização.

    def start(self):
        if self.worker:
            return
        def run():
            while not self.closed.wait(1):
                try:
                    self.process_once()
                except Exception:
                    logging.getLogger('drivebridge').warning('Fila SMTP temporariamente indisponível.')
        self.worker = threading.Thread(target=run, name='drivebridge-email', daemon=True)
        self.worker.start()
