import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from drivebridge.core import config_dir, save_profiles, write_json
from drivebridge.web import SyncManager, create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env=patch.dict(os.environ, {'XDG_CONFIG_HOME':self.tmp.name,'DRIVEBRIDGE_WEB_PASSWORD':'test-password','DRIVEBRIDGE_PUBLIC_URL':'http://localhost:8765'})
        self.env.start();self.addCleanup(self.env.stop)
        self.manager=SyncManager()
        self.app=create_app(self.manager)
        self.app.testing=True
        self.client=self.app.test_client()
        self.client.get('/login')
        with self.client.session_transaction() as session:
            csrf=session['csrf']
        response=self.client.post('/login',data={'password':'test-password','csrf':csrf})
        self.assertEqual(response.status_code,302)
        with self.client.session_transaction() as session:
            self.csrf=session['csrf']
        self.headers={'X-CSRF-Token':self.csrf}
        write_json(config_dir()/'accounts.json',{'test':{'emailAddress':'test@example.com'}})

    def profile(self,**updates):
        data={'name':'Test','account':'test','local':self.tmp.name,'remote':'root','direction':'both','enabled':False,'interval':15,'workers':4,'chunk_mib':16}
        data.update(updates)
        return data

    def test_page_contains_all_primary_controls(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        for item in ('Sincronizar agora','Agendamento','Pasta local no Debian','TAXA TOTAL','Adicionar conta','Bidirecional','Baixar registros'):
            self.assertIn(item,response.get_data(as_text=True))

    def test_unauthenticated_read_and_csrf_write_rejected(self):
        other=self.app.test_client()
        self.assertEqual(other.get('/api/state').status_code,401)
        self.assertEqual(self.client.post('/api/profiles',json=self.profile()).status_code,403)
        self.assertEqual(self.client.get('/api/state',headers={'Host':'attacker.test'}).status_code,400)

    def test_profiles_and_root_preserved(self):
        response=self.client.post('/api/profiles',json=self.profile(workers=8,chunk_mib=32),headers=self.headers)
        self.assertEqual(response.status_code,200)
        saved=response.json
        state=self.client.get('/api/state').json
        self.assertEqual(state['profiles'][saved['id']]['remote'],'root')
        self.assertEqual(state['profiles'][saved['id']]['workers'],8)
        self.assertIn('test',state['accounts'])
        self.assertEqual(self.client.delete('/api/profiles/'+saved['id'],headers=self.headers).status_code,200)

    def test_remote_browser_and_local_picker(self):
        response=self.client.get('/api/folders/local?path='+self.tmp.name)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['path'],self.tmp.name)
        with patch('drivebridge.web.Drive') as drive:
            drive.return_value.children.return_value=[{'id':'folder','name':'Folder'}]
            response=self.client.get('/api/folders/remote?account=test&parent=root')
            self.assertEqual(response.json['folders'][0]['id'],'folder')
            drive.return_value.children.assert_called_once_with('root',True)

    def test_local_picker_recovers_migrated_path_and_navigates(self):
        missing = self.tmp.name + '/missing/fedora/disk'
        response = self.client.get('/api/folders/local', query_string={'path':missing,'fallback':'home'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['path'], str(Path.home().resolve()))
        self.assertTrue(response.json['notice'])
        self.assertEqual(self.client.get('/api/folders/local',query_string={'path':missing}).status_code,400)
        child=Path(self.tmp.name)/'Disco com espaços'
        child.mkdir()
        response=self.client.get('/api/folders/local',query_string={'path':self.tmp.name})
        self.assertIn({'name':child.name,'id':str(child)},response.json['folders'])
        response=self.client.get('/api/folders/local',query_string={'path':str(child)})
        self.assertEqual(response.json['parent'],self.tmp.name)

    def test_hour_and_day_schedules_survive_reload(self):
        for minutes in (6*60, 3*1440, 30*1440):
            response=self.client.post('/api/profiles',json=self.profile(interval=minutes,enabled=True),headers=self.headers)
            self.assertEqual(response.status_code,200)
            identifier=response.json['id']
            state=self.client.get('/api/state').json
            self.assertEqual(state['profiles'][identifier]['interval'],minutes)
        self.assertEqual(self.client.post('/api/profiles',json=self.profile(interval=525601),headers=self.headers).status_code,400)

    def test_invalid_performance_and_unknown_account_rejected(self):
        for data in (self.profile(workers=999),self.profile(chunk_mib=0),self.profile(account='missing')):
            self.assertEqual(self.client.post('/api/profiles',json=data,headers=self.headers).status_code,400)

    def test_calendar_schedule_and_overwrite_validation(self):
        schedule={'frequency':'weeks','every':2,'start':'time','time':'08:30','weekday':2,'day':1}
        response=self.client.post('/api/profiles',json=self.profile(schedule=schedule,overwrite='newer'),headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['schedule'],schedule)
        self.assertTrue(response.json['enabled'])
        self.assertEqual(response.json['interval'],20160)
        for changes in ({'schedule':dict(schedule,time='99:20')},{'schedule':dict(schedule,every=0)},{'overwrite':'always'}):
            self.assertEqual(self.client.post('/api/profiles',json=self.profile(**changes),headers=self.headers).status_code,400)
        response=self.client.post('/api/profiles',json=self.profile(schedule=dict(schedule,frequency='manual')),headers=self.headers)
        self.assertFalse(response.json['enabled'])

    def test_schedule_starts_even_when_page_not_open(self):
        profile=self.profile(id='scheduled',enabled=True)
        save_profiles({'scheduled':profile})
        self.manager.due['scheduled']=time.monotonic()-1
        with patch.object(self.manager,'start') as start:
            self.manager.schedule_once()
            start.assert_called_once_with('scheduled')

    def test_email_settings_secret_and_test_recipient(self):
        settings={'enabled':True,'host':'smtp.example.com','port':587,'security':'starttls','username':'sender@example.com','sender':'sender@example.com','password':'secret'}
        response=self.client.post('/api/email',json=settings,headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertNotIn('password',response.json)
        self.assertTrue(self.client.get('/api/email').json['password_saved'])
        with patch.object(self.manager.notifier,'enqueue') as enqueue:
            self.assertEqual(self.client.post('/api/email/test',json={'account':'test'},headers=self.headers).status_code,200)
            self.assertEqual(enqueue.call_args.kwargs['recipient'],'test@example.com')
        self.assertEqual(self.client.post('/api/email',json=settings).status_code,403)

    def test_planned_schedule_survives_service_restart(self):
        schedule={'frequency':'days','every':2,'start':'time','time':'12:00','weekday':0,'day':1}
        save_profiles({'scheduled':self.profile(id='scheduled',enabled=True,schedule=schedule)})
        self.manager.schedule_once()
        expected=self.manager.schedule_records['scheduled']['due']
        restarted=SyncManager()
        restarted.schedule_once()
        self.assertEqual(restarted.schedule_records['scheduled']['due'],expected)

    def test_parallel_metrics_and_summary(self):
        now=time.monotonic()
        self.manager.emit('phase','transferring')
        self.manager.emit('progress',(0,2048,'',0,2,now-1,'',0,()))
        self.manager.emit('progress',(1024,2048,'a',0,2,now,'download',1024,(('a','download'),('b','upload'))))
        result=self.manager.snapshot()
        self.assertEqual(result['progress']['percent'],50)
        self.assertEqual(len(result['progress']['active']),2)
        self.assertIn('/s',result['progress']['rate'])

    def test_busy_sync_and_edits_are_blocked(self):
        self.manager.busy=True
        self.assertEqual(self.client.post('/api/profiles',json=self.profile(),headers=self.headers).status_code,400)
        self.assertEqual(self.client.post('/api/sync/test',headers=self.headers).status_code,400)
        self.assertEqual(self.client.post('/api/cancel',headers=self.headers).status_code,200)
        self.assertTrue(self.manager.cancel.is_set())

    def test_oauth_config_validated_without_overwriting_desktop_client(self):
        installed={'installed':{'client_id':'test','client_secret':'test','auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'}}
        write_json(config_dir()/'oauth-client.json',installed)
        web={'web':installed['installed']}
        response=self.client.post('/api/oauth/config',data={'file':(io.BytesIO(json.dumps(web).encode()),'client.json')},headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(json.loads((config_dir()/'oauth-client.json').read_text()),installed)
        self.assertTrue((config_dir()/'oauth-web-client.json').exists())

    def test_work_survives_browser_disconnect(self):
        profile=self.profile(id='test')
        save_profiles({'test':profile})
        arrived=threading.Event();release=threading.Event()
        def sync(profile,emit,cancel):
            arrived.set();release.wait(3);emit('status','Concluído: 1 transferências')
        with patch('drivebridge.web.synchronize',side_effect=sync):
            self.assertEqual(self.client.post('/api/sync/test',headers=self.headers).status_code,200)
            self.assertTrue(arrived.wait(2))
            self.client.post('/logout',headers=self.headers)
            self.assertTrue(self.manager.busy)
            release.set()
            for _ in range(100):
                if not self.manager.busy:break
                time.sleep(.01)
            self.assertFalse(self.manager.busy)
            self.assertIn('Concluído',self.manager.status)
