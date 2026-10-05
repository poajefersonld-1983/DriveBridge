"""Interface própria e leve em Tkinter; chamadas de rede fora da interface."""
import queue
import logging
import os
import re
import webbrowser
import threading
import time
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from .core import Drive, accounts, connect, connection_error, config_dir, import_oauth_client, load_profiles, oauth_client_file, save_profiles, synchronize

from .progress import TransferStats, format_bytes

DIRECTIONS = {'Local → Google Drive': 'upload', 'Google Drive → Local': 'download', 'Bidirecional ↔': 'both'}


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.busy = False
        self.transfer_stats = TransferStats()
        self.transfer_stats.active = False
        self.transfer_phase = None
        self.profiles = load_profiles()
        self.account_records = accounts()
        self.next_runs = {}
        self.current_id = None
        root.title('DriveBridge • Sincronização de pastas')
        root.geometry('1000x760')
        root.minsize(780, 600)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TButton', padding=7)
        style.configure('TLabel', padding=3)
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill='both', expand=True)
        footer = ttk.LabelFrame(frame, text='Atividade em tempo real', padding=10)
        footer.pack(side='bottom', fill='x', pady=(12, 0))
        self.status = tk.StringVar(value='Pronto. Adicione uma conta e configure um par de pastas.')
        self.details = tk.StringVar(value='0 B / 0 B • 0/0 arquivos')
        self.current_file = tk.StringVar(value='Arquivo atual: nenhum')
        self.transfer_rate = tk.StringVar(value='Taxa: 0 B/s • Média: 0 B/s')
        status_label = ttk.Label(footer, textvariable=self.status, wraplength=700)
        status_label.pack(fill='x')
        self.progress = ttk.Progressbar(footer, maximum=100, mode='determinate')
        self.progress.pack(fill='x', pady=5)
        ttk.Label(footer, textvariable=self.details).pack(fill='x')
        ttk.Label(footer, textvariable=self.transfer_rate).pack(fill='x')
        file_label = ttk.Label(footer, textvariable=self.current_file, wraplength=700)
        file_label.pack(fill='x')
        footer.bind('<Configure>', lambda event: (status_label.configure(wraplength=max(200, event.width - 30)), file_label.configure(wraplength=max(200, event.width - 30))))
        ttk.Label(frame, text='DriveBridge', font=('', 24, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Suas pastas, conectadas ao Google Drive.').pack(anchor='w')
        header = ttk.Frame(frame)
        header.pack(fill='x', pady=12)
        ttk.Label(header, text='Conta Google').pack(side='left')
        self.account = ttk.Combobox(header, state='readonly', width=38)
        self.account.pack(side='left', padx=10)
        ttk.Button(header, text='Adicionar conta', command=self.add_account).pack(side='left')
        self.refresh_accounts()
        body = ttk.Panedwindow(frame, orient='horizontal')
        body.pack(fill='both', expand=True)
        left = ttk.Frame(body, padding=8)
        right_container = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right_container, weight=3)
        canvas = tk.Canvas(right_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(right_container, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        right = ttk.Frame(canvas, padding=12)
        content = canvas.create_window((0, 0), window=right, anchor='nw')
        right.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(content, width=event.width))
        canvas.bind('<Button-4>', lambda event: canvas.yview_scroll(-1, 'units'))
        canvas.bind('<Button-5>', lambda event: canvas.yview_scroll(1, 'units'))
        ttk.Label(left, text='Pares de pastas', font=('', 12, 'bold')).pack(anchor='w')
        self.pairs = tk.Listbox(left, exportselection=False, width=24, relief='flat')
        self.pairs.pack(fill='both', expand=True, pady=8)
        self.pairs.bind('<<ListboxSelect>>', self.select)
        ttk.Button(left, text='+ Novo par', command=self.new).pack(fill='x')
        ttk.Button(left, text='Remover par', command=self.remove).pack(fill='x', pady=6)
        self.name = tk.StringVar()
        self.local = tk.StringVar()
        self.remote = tk.StringVar(value='root')
        self.direction = tk.StringVar(value='Bidirecional ↔')
        self.interval = tk.StringVar(value='0')
        self.enabled = tk.BooleanVar(value=False)
        for label, variable in [('Nome do par', self.name), ('Pasta local', self.local), ('Pasta no Drive (ID)', self.remote)]:
            ttk.Label(right, text=label).pack(anchor='w')
            row = ttk.Frame(right)
            row.pack(fill='x', pady=(0, 7))
            ttk.Entry(row, textvariable=variable).pack(side='left', fill='x', expand=True)
            if variable is self.local:
                ttk.Button(row, text='Escolher…', command=self.choose_local).pack(side='left', padx=5)
            elif variable is self.remote:
                ttk.Button(row, text='Navegar…', command=self.choose_remote).pack(side='left', padx=5)
        ttk.Label(right, text='Direção da sincronização').pack(anchor='w')
        ttk.Combobox(right, textvariable=self.direction, values=list(DIRECTIONS), state='readonly').pack(fill='x', pady=(0, 10))
        schedule = ttk.Frame(right)
        schedule.pack(fill='x')
        ttk.Checkbutton(schedule, text='Agendar a cada', variable=self.enabled).pack(side='left')
        ttk.Spinbox(schedule, from_=1, to=10080, textvariable=self.interval, width=7).pack(side='left', padx=5)
        ttk.Label(schedule, text='minutos, enquanto o aplicativo estiver aberto').pack(side='left')
        ttk.Label(right, text='Conflitos são sinalizados. Exclusões não são propagadas.', wraplength=450).pack(anchor='w', pady=8)
        actions = ttk.Frame(right)
        actions.pack(fill='x', pady=6)
        ttk.Button(actions, text='Salvar par', command=self.save).pack(side='left')
        ttk.Button(actions, text='Sincronizar agora', command=self.start).pack(side='left', padx=8)
        ttk.Button(actions, text='Cancelar', command=self.cancelled.set).pack(side='left')
        self.log = ScrolledText(right, height=4, state='disabled', wrap='word', relief='flat')
        self.log.pack(fill='both', expand=True, pady=8)
        self.refresh_pairs()
        if self.pair_ids:
            self.pairs.selection_set(0)
            self.select()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def emit(self, kind, data):
        if kind in ('log', 'error', 'status'):
            logging.getLogger('drivebridge').info('%s: %s', kind, data)
        self.events.put((kind, data))

    def worker(self, function):
        if self.busy:
            messagebox.showinfo('Aguarde', 'Há uma operação em andamento.')
            return
        self.busy = True
        self.cancelled.clear()
        self.status.set('Conectando…')
        def run():
            try:
                function()
            except InterruptedError:
                self.emit('status', 'Operação cancelada.')
            except Exception as error:
                logging.getLogger('drivebridge').exception('Falha na operação')
                self.emit('error', str(error))
            finally:
                self.emit('done', None)
        threading.Thread(target=run, daemon=True).start()

    def refresh_accounts(self):
        self.account_records = accounts()
        self.account_ids = list(self.account_records)
        self.account.configure(values=[self.account_records[k]['emailAddress'] for k in self.account_ids])
        if self.account_ids and self.account.current() < 0:
            self.account.current(0)

    def add_account(self):
        if self.busy:
            messagebox.showinfo('Aguarde', 'Há uma operação em andamento.')
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('Conectar ao Google Drive')
        dialog.geometry('640x360')
        dialog.minsize(520, 340)
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill='both', expand=True)
        actions = ttk.Frame(frame)
        actions.pack(side='bottom', fill='x', pady=(12, 0))
        ttk.Label(frame, text='Conectar sua conta', font=('', 18, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Informe o e-mail e confirme a autorização no navegador.', wraplength=470).pack(anchor='w', pady=8)
        email = tk.StringVar()
        ttk.Label(frame, text='E-mail da conta Google').pack(anchor='w')
        entry = ttk.Entry(frame, textvariable=email)
        entry.pack(fill='x', pady=6)
        entry.focus_set()
        connection_status = tk.StringVar(value='Pronto para entrar com Google.' if oauth_client_file() else 'Falta configurar o acesso Google do DriveBridge. Clique em Configurar acesso Google; isso é feito uma única vez.')
        ttk.Label(frame, textvariable=connection_status, wraplength=470).pack(anchor='w', pady=6)
        ttk.Label(frame, text='Após autorizar, sua conexão será salva neste computador.', wraplength=470).pack(anchor='w', pady=6)
        def setup():
            self.oauth_setup(dialog, lambda: connection_status.set('Configuração salva. Clique em Entrar com Google.'))
        def login():
            address = email.get().strip()
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', address):
                messagebox.showerror('Verifique o e-mail', 'Informe um e-mail válido da sua conta Google.', parent=dialog)
                return
            client = oauth_client_file()
            if not client:
                connection_status.set('Falta o cliente OAuth do aplicativo. Siga o assistente e importe o JSON para liberar o login.')
                setup()
                return
            def authorize():
                try:
                    identifier = connect(client, address, self.emit)
                    self.emit('connected', identifier)
                except Exception as error:
                    self.emit('auth_error', connection_error(error))
            dialog.destroy()
            self.worker(authorize)
        ttk.Button(actions, text='Configurar acesso Google', command=setup).pack(side='left')
        ttk.Button(actions, text='Entrar com Google', command=login).pack(side='right')
        entry.bind('<Return>', lambda event: login())

    def oauth_setup(self, parent, configured):
        existing = getattr(self, 'oauth_setup_dialog', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        dialog = tk.Toplevel(parent)
        self.oauth_setup_dialog = dialog
        dialog.title('Configurar acesso Google • DriveBridge')
        dialog.geometry('640x520')
        dialog.minsize(520, 400)
        dialog.transient(parent)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill='both', expand=True)
        # Reserve as ações antes do texto para evitar recortes em telas pequenas.
        controls = ttk.Frame(frame)
        controls.pack(side='bottom', fill='x', pady=(10, 0))
        ttk.Label(frame, text='Preparar o login Google', font=('', 16, 'bold')).pack(anchor='w')
        instructions = ScrolledText(frame, wrap='word', height=8, relief='flat', padx=10, pady=10)
        instructions.pack(fill='both', expand=True, pady=8)
        instructions.insert('1.0',
            'O aplicativo ainda não tem um cliente OAuth cadastrado. O e-mail sozinho não autoriza acesso ao Drive. Este cadastro é feito uma vez para o DriveBridge.\n\n'
            '1. Abra o Google Cloud, faça login e crie ou selecione um projeto.\n\n'
            '2. No mesmo projeto, habilite a Google Drive API.\n\n'
            '3. No Google Auth Platform, configure Branding, Audience e Data Access. '
            'Se estiver em teste, inclua seu e-mail nos usuários de teste e configure o escopo https://www.googleapis.com/auth/drive.\n\n'
            '4. Abra Clients, crie um cliente do tipo Aplicativo para computador e baixe o arquivo JSON.\n\n'
            '5. Clique em Importar JSON abaixo. Depois volte à tela de e-mail e clique em Entrar com Google.\n\n'
            'Entrar no Google Cloud não conecta o DriveBridge. A autorização do Drive será aberta após importar a configuração. Na distribuição configurada, os demais usuários não precisarão fazer estes passos.')
        instructions.configure(state='disabled')
        for title, url in [
            ('1. Abrir Google Cloud', 'https://console.cloud.google.com/'),
            ('2. Habilitar Drive API', 'https://console.cloud.google.com/apis/library/drive.googleapis.com'),
            ('3. Configurar consentimento', 'https://console.cloud.google.com/auth/overview'),
            ('4. Criar cliente e baixar JSON', 'https://console.cloud.google.com/auth/clients'),
        ]:
            ttk.Button(controls, text=title, command=lambda address=url: webbrowser.open(address)).pack(fill='x', pady=2)
        def import_file():
            path = filedialog.askopenfilename(parent=dialog, title='Importar JSON OAuth para computador', filetypes=[('OAuth JSON', '*.json')])
            if path:
                try:
                    import_oauth_client(path)
                    if not oauth_client_file():
                        raise ValueError('Uma configuração externa DRIVEBRIDGE_OAUTH_CLIENT aponta para um arquivo ausente. Corrija essa configuração para continuar.')
                    configured()
                    dialog.destroy()
                except (ValueError, OSError) as error:
                    messagebox.showerror('Arquivo inválido', str(error), parent=dialog)
        ttk.Button(controls, text='5. Importar JSON…', command=import_file).pack(fill='x', pady=(8, 2))

    def show_connection_error(self, error):
        dialog = tk.Toplevel(self.root)
        dialog.title(error['title'])
        dialog.geometry('640x380')
        dialog.minsize(480, 300)
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill='both', expand=True)
        controls = ttk.Frame(frame)
        controls.pack(side='bottom', fill='x', pady=(12, 0))
        if error.get('url'):
            ttk.Button(controls, text='Abrir configuração no Google Cloud', command=lambda: webbrowser.open(error['url'])).pack(side='left')
        ttk.Button(controls, text='Fechar', command=dialog.destroy).pack(side='right')
        ttk.Label(frame, text=error['title'], font=('', 15, 'bold')).pack(anchor='w')
        text = ScrolledText(frame, wrap='word', relief='flat', padx=8, pady=8)
        text.pack(fill='both', expand=True, pady=8)
        text.insert('1.0', error['message'])
        text.configure(state='disabled')

    def choose_local(self):
        path = filedialog.askdirectory()
        if path:
            self.local.set(path)

    def choose_remote(self):
        if self.account.current() < 0:
            messagebox.showinfo('Conta', 'Adicione uma conta primeiro.')
            return
        account_id = self.account_ids[self.account.current()]
        self.worker(lambda: self.emit('folders', ('root', [('root', 'Meu Drive')], Drive(account_id).children('root', True), account_id)))

    def folder_dialog(self, data):
        parent, trail, folders, account_id = data
        dialog = tk.Toplevel(self.root)
        dialog.title('Escolher pasta do Google Drive')
        dialog.geometry('500x380')
        ttk.Label(dialog, text=' / '.join(name for _, name in trail)).pack(anchor='w', padx=12, pady=8)
        listing = tk.Listbox(dialog)
        listing.pack(fill='both', expand=True, padx=12)
        for item in folders:
            listing.insert('end', item['name'])
        def open_selected(event=None):
            selected = listing.curselection()
            if selected:
                item = folders[selected[0]]
                dialog.destroy()
                self.worker(lambda: self.emit('folders', (item['id'], trail + [(item['id'], item['name'])], Drive(account_id).children(item['id'], True), account_id)))
        def up():
            if len(trail) > 1:
                dialog.destroy()
                previous = trail[:-1]
                self.worker(lambda: self.emit('folders', (previous[-1][0], previous, Drive(account_id).children(previous[-1][0], True), account_id)))
        def choose():
            self.remote.set(parent)
            dialog.destroy()
        listing.bind('<Double-1>', open_selected)
        row = ttk.Frame(dialog, padding=12)
        row.pack(fill='x')
        ttk.Button(row, text='Voltar', command=up).pack(side='left')
        ttk.Button(row, text='Abrir pasta', command=open_selected).pack(side='left', padx=6)
        ttk.Button(row, text='Usar esta pasta', command=choose).pack(side='right')

    def refresh_pairs(self):
        self.pair_ids = list(self.profiles)
        self.pairs.delete(0, 'end')
        for key in self.pair_ids:
            profile = self.profiles[key]
            self.pairs.insert('end', profile['name'])
            if profile.get('enabled'):
                self.next_runs.setdefault(key, time.monotonic() + profile['interval'] * 60)

    def new(self):
        self.current_id = None
        self.name.set('Novo par')
        self.local.set('')
        self.remote.set('root')
        self.enabled.set(False)
        self.interval.set('15')

    def select(self, event=None):
        selection = self.pairs.curselection()
        if not selection:
            return
        self.current_id = self.pair_ids[selection[0]]
        p = self.profiles[self.current_id]
        self.name.set(p['name'])
        self.local.set(p['local'])
        self.remote.set(p['remote'])
        self.direction.set(next(k for k, v in DIRECTIONS.items() if v == p['direction']))
        self.interval.set(str(p['interval']))
        self.enabled.set(p['enabled'])
        if p['account'] in self.account_ids:
            self.account.current(self.account_ids.index(p['account']))

    def save(self):
        try:
            if self.account.current() < 0 or not self.name.get().strip() or not self.local.get().strip() or not self.remote.get().strip():
                raise ValueError('Informe conta, nome e as duas pastas.')
            interval = int(self.interval.get())
            if self.enabled.get() and interval < 1:
                raise ValueError('Use um intervalo de pelo menos 1 minuto.')
            identifier = self.current_id or uuid.uuid4().hex
            p = dict(id=identifier, name=self.name.get().strip(), account=self.account_ids[self.account.current()],
                local=self.local.get().strip(), remote=self.remote.get().strip(), direction=DIRECTIONS[self.direction.get()],
                interval=interval, enabled=self.enabled.get())
            previous = self.profiles.get(identifier, {})
            p['workers'] = previous.get('workers', 4)
            p['chunk_mib'] = previous.get('chunk_mib', 16)
            self.profiles[identifier] = p
            save_profiles(self.profiles)
            self.current_id = identifier
            self.next_runs.pop(identifier, None)
            self.refresh_pairs()
            return p.copy()
        except (ValueError, OSError) as error:
            messagebox.showerror('Verifique a configuração', str(error))

    def remove(self):
        if self.current_id and not self.busy:
            self.profiles.pop(self.current_id, None)
            save_profiles(self.profiles)
            self.next_runs.pop(self.current_id, None)
            self.new()
            self.refresh_pairs()

    def start(self):
        if self.busy:
            messagebox.showinfo('Operação em andamento', 'A sincronização ou outra operação ainda está em andamento. Confira a atividade no rodapé ou clique em Cancelar.')
            return
        profile = self.save()
        if profile:
            self.emit('log', f'Iniciando sincronização: {profile["name"]} • remoto: {profile["remote"]}')
            self.worker(lambda: synchronize(profile, self.emit, self.cancelled))

    def close(self):
        if self.busy:
            self.cancelled.set()
            self.status.set('Cancelando… aguarde a chamada de rede terminar para sair.')
            self.root.after(200, self.close)
        else:
            self.root.destroy()

    def poll(self):
        while not self.events.empty():
            kind, data = self.events.get()
            if kind == 'status':
                self.status.set(data)
                if data.startswith('Concluído:') or data == 'Operação cancelada.':
                    self.emit('log', data)
            elif kind == 'accounts':
                self.refresh_accounts()
            elif kind == 'connected':
                self.refresh_accounts()
                self.account.current(self.account_ids.index(data))
                self.status.set('Conectado: ' + self.account_records[data]['emailAddress'])
            elif kind == 'auth_error':
                self.status.set(data['title'])
                self.show_connection_error(data)
            elif kind == 'folders':
                self.folder_dialog(data)
            elif kind == 'error':
                self.emit('log', 'ERRO: ' + data)
                self.status.set('Operação não concluída: ' + data)
                self.show_connection_error({'title': 'Sincronização interrompida', 'message': data + '\n\nOs arquivos já concluídos foram mantidos. Corrija a causa e execute novamente; consulte também o registro de atividade.', 'url': None})
            elif kind == 'done':
                self.busy = False
                self.transfer_stats.finished = time.monotonic()
                self.transfer_stats.active = False
                self.progress.stop()
                self.progress.configure(mode='determinate')
                if self.transfer_phase == 'scanning':
                    self.progress['value'] = 0
                self.transfer_phase = None
            elif kind == 'log':
                self.log.configure(state='normal')
                self.log.insert('end', data + '\n')
                self.log.see('end')
                self.log.configure(state='disabled')
            elif kind == 'phase':
                self.transfer_phase = data
                self.progress.stop()
                self.progress['value'] = 0
                if data == 'scanning':
                    self.progress.configure(mode='indeterminate')
                    self.progress.start(30)
                    self.details.set('Varredura em andamento • total ainda não calculado')
                    self.current_file.set('Arquivo atual: verificando pastas e checksums')
                    self.transfer_rate.set('Taxa: — • Nenhum arquivo sendo transferido ainda')
                    self.transfer_stats.reset(time.monotonic())
                    self.transfer_stats.active = False
                else:
                    self.progress.configure(mode='determinate')
                    self.transfer_stats.reset(time.monotonic())
            elif kind == 'progress':
                done, total, name, count, number, timestamp, action = data[:7]
                measured_bytes = data[7] if len(data) > 7 else done
                if count == 0 and not name:
                    self.transfer_stats.reset(timestamp)
                self.transfer_stats.update(measured_bytes, total, timestamp)
                percent = 100 * done / total if total else (100 if count == number else 0)
                if count < number:
                    percent = min(percent, 99.9)
                self.progress['value'] = percent
                self.details.set(f'{percent:.1f}% • {format_bytes(done)} / {format_bytes(total)} • {count}/{number} arquivos')
                direction = {'upload': 'Enviando', 'download': 'Baixando'}.get(action, 'Arquivo atual')
                if len(data) > 8:
                    running = data[8]
                    names = ' | '.join(('↑ ' if mode == 'upload' else '↓ ') + path.rsplit('/', 1)[-1][:45] for path, mode in running)
                    self.current_file.set(f'Ativos ({len(running)}): {names}' if running else 'Arquivo atual: nenhum')
                else:
                    self.current_file.set(f'{direction}: {name}' if name else 'Arquivo atual: nenhum')
        if self.transfer_phase == 'transferring' or self.transfer_stats.done:
            rate, average = self.transfer_stats.rates(time.monotonic())
            self.transfer_rate.set(f'Taxa: {format_bytes(rate)}/s • Média: {format_bytes(average)}/s')
        if not self.busy:
            for key, due in list(self.next_runs.items()):
                p = self.profiles.get(key)
                if p and p['enabled'] and time.monotonic() >= due:
                    self.next_runs[key] = time.monotonic() + p['interval'] * 60
                    self.worker(lambda profile=p.copy(): synchronize(profile, self.emit, self.cancelled))
                    break
        self.root.after(100, self.poll)


def main():
    path = config_dir() / 'activity.log'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger = logging.getLogger('drivebridge')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    root = tk.Tk()
    def callback_error(exc_type, value, traceback):
        logger.error('Falha da interface', exc_info=(exc_type, value, traceback))
        messagebox.showerror('Falha da interface', str(value) + '\nConsulte o registro de atividade em ' + str(path))
    root.report_callback_exception = callback_error
    App(root).root.mainloop()


if __name__ == '__main__':
    main()
