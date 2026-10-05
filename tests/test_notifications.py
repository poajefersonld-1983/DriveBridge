import gzip
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from drivebridge.core import config_dir, save_profiles, write_json
from drivebridge.notifications import Notifier, public_settings, send_message, validate_settings
from drivebridge.web import SyncManager


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'XDG_CONFIG_HOME':self.temp.name});env.start();self.addCleanup(env.stop)
        self.settings={'enabled':True,'host':'smtp.example.com','port':587,'security':'starttls','username':'sender@example.com','password':'secret','sender':'sender@example.com','recipients':[]}
        write_json(config_dir()/'email-settings.json',self.settings)

    def test_configuration_preserves_password_without_exposing_it(self):
        data=dict(self.settings,password='')
        self.assertEqual(validate_settings(data)['password'],'secret')
        self.assertNotIn('password',public_settings())
        self.assertTrue(public_settings()['password_saved'])
        with self.assertRaises(ValueError):
            validate_settings(dict(data,security='none'))

    def test_tls_and_full_compressed_attachment(self):
        log=Path(self.temp.name)/'run.log';log.write_text('arquivo\n'*2000)
        with patch('drivebridge.notifications.smtplib.SMTP') as factory:
            smtp=factory.return_value.__enter__.return_value
            smtp.send_message.return_value={}
            send_message(dict(self.settings,recipients=['connected@example.net']),'Resultado','Resumo',log)
            smtp.starttls.assert_called_once()
            smtp.login.assert_called_once_with('sender@example.com','secret')
            message=smtp.send_message.call_args.args[0]
            self.assertEqual(message['To'],'connected@example.net')
            attachment=list(message.iter_attachments())[0]
            self.assertEqual(gzip.decompress(attachment.get_payload(decode=True)).decode(),log.read_text())

    def test_recipient_is_frozen_for_each_operation_and_queue_survives_restart(self):
        notifier=Notifier(lambda *args:None)
        notifier.enqueue('Fim','Resumo',recipient='first@example.com')
        notifier.enqueue('Fim','Resumo',recipient='second@example.com')
        restarted=Notifier(lambda *args:None)
        recipients=[]
        with patch('drivebridge.notifications.send_message',side_effect=lambda settings,*args:recipients.append(settings['recipients'])):
            restarted.process_once();restarted.process_once()
        self.assertEqual(sorted(recipients),[['first@example.com'],['second@example.com']])
        self.assertTrue(all(json.loads(p.read_text())['state']=='sent' for p in notifier.folder.glob('*.json')))

    def test_failure_preserves_report_and_schedules_retry(self):
        notifier=Notifier(lambda *args:None)
        notifier.enqueue('Fim','Resumo',recipient='connected@example.com')
        with patch('drivebridge.notifications.send_message',side_effect=OSError('network')):
            notifier.process_once()
        job=json.loads(next(notifier.folder.glob('*.json')).read_text())
        self.assertEqual(job['attempts'],1);self.assertEqual(job['state'],'pending')
        self.assertGreater(job['due'],time.time())

    def test_success_cancel_and_failure_all_create_operation_report_for_pair_account(self):
        write_json(config_dir()/'accounts.json',{'one':{'emailAddress':'one@example.net'},'two':{'emailAddress':'two@example.net'}})
        for exception in (None, InterruptedError(), RuntimeError('falha')):
            manager=SyncManager()
            profile={'id':'pair','name':'Meu par','account':'two','local':self.temp.name,'remote':'root','direction':'both','enabled':False,'interval':15}
            save_profiles({'pair':profile})
            arrived=threading.Event();calls=[]
            def enqueue(*args,**kwargs):
                calls.append((args,kwargs));arrived.set()
            def sync(profile,emit,cancel):
                for index in range(1100):emit('log',f'Arquivo {index}')
                if exception:raise exception
                emit('status','Concluído: 0 transferências; 0 falhas pendentes')
            with patch('drivebridge.web.synchronize',side_effect=sync),patch.object(manager.notifier,'enqueue',side_effect=enqueue):
                manager.start('pair');self.assertTrue(arrived.wait(3))
            args,kwargs=calls[0]
            self.assertEqual(kwargs['recipient'],'two@example.net')
            content=Path(args[2]).read_text()
            self.assertIn('Arquivo 0',content);self.assertIn('Arquivo 1099',content)
            self.assertFalse(manager.busy)
