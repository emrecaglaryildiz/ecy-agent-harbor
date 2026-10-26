"""Eight platform phases use disposable storage, fake providers and local HTTP only."""
import base64
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import urllib.request
from urllib.error import HTTPError
from unittest.mock import patch
import zipfile

from app import server, platform, backup, pipeline
import test_harbor as helpers
import test_workspace as workspace_helpers


class PlatformTests(unittest.TestCase):
    setUpClass=classmethod(workspace_helpers.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(workspace_helpers.WorkspaceTests.tearDownClass.__func__)
    project=workspace_helpers.WorkspaceTests.project
    upload=workspace_helpers.WorkspaceTests.upload
    config=workspace_helpers.WorkspaceTests.config
    task=workspace_helpers.WorkspaceTests.task

    def setUp(self):
        with server.connect() as db:
            for table in ('finding_events','findings','knowledge','webhook_requests','webhooks'):
                db.execute('DELETE FROM '+table)
        workspace_helpers.WorkspaceTests.setUp(self)

    def completed(self, prompt='Question', output='Answer', **kw):
        tid=server.create_task(self.user,dict(self.config(**kw),prompt=prompt))
        with server.connect() as db:
            db.execute("UPDATE tasks SET status='completed',cost=0.25 WHERE id=?",(tid,))
            db.execute("INSERT INTO runs(id,task_id,agent_id,kind,status,output,input_tokens,output_tokens,cost,created_at) VALUES(?,?,'local','participant','completed',?,10,20,0.25,?)",(tid+'r',tid,output,server.now()))
        return tid

    def test_thread_retains_all_prompts_outputs_and_owner(self):
        first=self.completed('First question','First answer')
        _,r=self.client.request('/api/tasks/'+first+'/followup',{'prompt':'Second question'})
        second=r['id']
        with server.connect() as db:
            context=db.execute('SELECT continuation_context FROM tasks WHERE id=?',(second,)).fetchone()[0]
            self.assertIn('First question',context);self.assertIn('First answer',context)
            db.execute("UPDATE tasks SET status='completed' WHERE id=?",(second,))
        _,r=self.client.request('/api/tasks/'+second+'/followup',{'prompt':'Third question'})
        with server.connect() as db:
            row=db.execute('SELECT * FROM tasks WHERE id=?',(r['id'],)).fetchone()
            self.assertEqual(row['root_task'],first)
            self.assertIn('First answer',row['continuation_context'])
            self.assertIn('Second question',row['continuation_context'])
        _,data=self.client.request('/api/platform/thread?task='+r['id'])
        self.assertEqual(len(data['turns']),3)
        self.client.login('operator')
        code,_=self.client.request('/api/tasks/'+first+'/followup',{'prompt':'Other owner'})
        self.assertEqual(code,403)

    def test_followup_only_analyzes_new_evidence(self):
        old=self.upload('old evidence');new=self.upload('new evidence',name='new.log')
        first=self.completed(file_ids=[old])
        next_id=platform.followup(server,self.user,first,{'prompt':'Next','file_ids':[new]})
        with server.connect() as db:task=server.task_dict(db.execute('SELECT * FROM tasks WHERE id=?',(next_id,)).fetchone())
        calls=[]
        with patch.object(pipeline,'execute',side_effect=lambda *a,**kw:calls.append((a,kw)) or 'answer'):
            pipeline.participant(server,task,'local','prior discussion')
        self.assertIn('new evidence',calls[0][0][3])
        self.assertNotIn('old evidence',calls[0][0][3])
        self.assertIn('prior discussion',calls[-1][1]['context'])

    def test_compare_has_cost_tokens_and_plain_diff(self):
        a=self.completed(output='same\nold');b=self.completed(output='same\nnew')
        code,d=self.client.request('/api/platform/compare?ids='+a+','+b)
        self.assertEqual(code,200);self.assertEqual(d['items'][0]['input_tokens'],10)
        self.assertIn('-old',d['diffs'][0]['text']);self.assertIn('+new',d['diffs'][0]['text'])
        code,_=self.client.request('/api/platform/compare?ids='+a+','+a);self.assertEqual(code,400)

    def test_findings_validate_evidence_and_audit_transitions(self):
        pid=self.project();fid=self.upload(project=pid)
        body=dict(project_id=pid,title='Suspicious login',details='Investigate source',file_id=fid,first_line=1,last_line=2,severity='high',status='investigating')
        code,d=self.client.request('/api/platform/findings',body);self.assertEqual(code,201,d)
        code,_=self.client.request('/api/platform/findings',dict(body,id=d['id'],status='resolved'));self.assertEqual(code,201)
        _,r=self.client.request('/api/platform/findings?project_id='+pid)
        self.assertEqual([e['snapshot']['status'] for e in r['items'][0]['history']],['investigating','resolved'])
        code,_=self.client.request('/api/platform/findings',dict(body,project_id=None));self.assertEqual(code,400)
        self.client.login('viewer');code,_=self.client.request('/api/platform/findings',body);self.assertEqual(code,403)
        self.client.login('operator');code,_=self.client.request('/api/platform/findings',dict(body,id=d['id']));self.assertEqual(code,403)

    def test_knowledge_is_explicit_scoped_snapshot(self):
        pid=self.project();tid=self.completed(output='Exact previous report',project_id=pid)
        code,d=self.client.request('/api/platform/knowledge',dict(project_id=pid,source_task=tid,title='Saved report'));self.assertEqual(code,201)
        kid=d['id'];code,t=self.client.request('/api/tasks',self.config(project_id=pid,knowledge_ids=[kid]));self.assertEqual(code,201,t)
        with server.connect() as db:
            row=db.execute('SELECT continuation_context FROM tasks WHERE id=?',(t['id'],)).fetchone()
            self.assertIn('[knowledge:'+kid+']',row[0]);self.assertIn('Exact previous report',row[0])
        code,_=self.client.request('/api/tasks',self.config(knowledge_ids=[kid]));self.assertEqual(code,400)
        _,d=self.client.request('/api/platform/knowledge?project_id='+pid+'&q=previous')
        self.assertEqual(len(d['reports']),1);self.assertEqual(len(d['items']),1)

    def test_activity_and_model_snapshot_hide_provider_state(self):
        tid=self.task()
        server.run_agent(tid,'local','Test')
        _,d=self.client.request('/api/platform/activity')
        self.assertEqual(d['tasks'][0]['id'],tid)
        self.assertTrue(d['runs'][0]['finished_at'])
        self.assertNotIn('provider_state',d['runs'][0])
        with server.connect() as db:
            row=db.execute('SELECT model_snapshot FROM runs WHERE task_id=?',(tid,)).fetchone()
            self.assertEqual(row[0],server.BY_ID['local']['model'])

    def test_capability_validation_and_incompatible_tasks(self):
        body=dict(server.settings_agent(server.BY_ID['local']),endpoint='http://127.0.0.1:9/v1',capabilities={'vision':False,'tools':False,'context_tokens':32000,'specialties':['log']})
        aid=server.save_agent(self.user,body)
        self.assertEqual(server.public_agent(server.BY_ID[aid])['capabilities']['context_tokens'],32000)
        code,d=self.client.request('/api/files',{'name':'image.png','data':base64.b64encode(b'\x89PNG\r\n\x1a\n').decode()})
        self.assertEqual(code,201)
        code,_=self.client.request('/api/tasks',self.config(file_ids=[d['id']]))
        self.assertEqual(code,400)
        with self.assertRaises(ValueError):platform.capabilities({'capabilities':{'vision':'yes'}},{})

    def test_encrypted_backup_restore_and_automation_suspension(self):
        fid=self.upload('sensitive evidence')
        server.encrypt(server.DB_PATH,'fake-test-key')
        tid=server.create_task(self.user,self.config(file_ids=[fid]))
        with server.connect() as db:db.execute("UPDATE tasks SET status='awaiting_approval' WHERE id=?",(tid,))
        content=backup.create(server,'disposable-password-123')
        self.assertNotIn(b'sensitive evidence',content)
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'restored'
            backup.restore(content,'disposable-password-123',target)
            self.assertEqual((target/'uploads'/fid).read_text(),'sensitive evidence')
            self.assertEqual((target/'master.key').read_bytes(),(Path(server.DB_PATH).parent/'master.key').read_bytes())
            with sqlite3.connect(target/'harbor.db') as db:self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'incomplete')
            with self.assertRaises(ValueError):backup.restore(content,'disposable-password-123',target)
            with self.assertRaises(ValueError):backup.restore(content,'wrong-password-123',Path(temp)/'other')
            broken=bytearray(content);broken[-20]^=1
            with self.assertRaises(ValueError):backup.restore(bytes(broken),'disposable-password-123',Path(temp)/'broken')

    def test_backup_rejects_authenticated_path_traversal(self):
        salt=b'x'*16;out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('../escape','bad')
        content=backup.MAGIC+salt+backup.cipher('disposable-password-123',salt).encrypt(out.getvalue())
        with self.assertRaises(ValueError):backup.unpack(content,'disposable-password-123')

    def test_webhook_scoped_token_idempotency_and_revocation(self):
        code,h=self.client.request('/api/platform/webhooks',{'name':'Hermes','config':self.config()})
        self.assertEqual(code,201,h)
        _,listing=self.client.request('/api/platform/webhooks');self.assertNotIn(h['token'],json.dumps(listing))
        def send(token,key):
            request=urllib.request.Request(helpers.BASE+'/api/hooks/'+h['id'],data=json.dumps({'prompt':'Inbound','agents':['critic'],'tool_requested':True}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token,'Idempotency-Key':key})
            try:response=urllib.request.urlopen(request)
            except HTTPError as e:response=e
            return response.code,json.loads(response.read())
        code,a=send(h['token'],'one');self.assertEqual(code,201,a)
        code,b=send(h['token'],'one');self.assertEqual(code,200,b);self.assertEqual(a['id'],b['id'])
        with server.connect() as db:
            row=db.execute('SELECT agents,tool_requested FROM tasks WHERE id=?',(a['id'],)).fetchone();self.assertEqual(json.loads(row[0]),['local']);self.assertEqual(row[1],0)
        self.assertEqual(send('invalid','two')[0],403)
        self.client.request('/api/platform/webhooks/revoke',{'id':h['id']})
        self.assertEqual(send(h['token'],'two')[0],403)

    def test_admin_only_backup_and_webhooks_and_viewer_reads(self):
        for role in ('operator','viewer'):
            self.client.login(role)
            for path,body in [('/api/platform/backup',{'password':'disposable-password-123'}),('/api/platform/webhooks',{'name':'Denied','config':self.config()})]:
                code,_=self.client.request(path,body);self.assertEqual(code,403)
        for path in ('/api/platform/findings','/api/platform/knowledge','/api/platform/activity'):
            code,_=self.client.request(path);self.assertEqual(code,200)

    def test_restore_disables_webhooks_schedules_and_telegram(self):
        self.client.request('/api/platform/webhooks',{'name':'Hook','config':self.config()})
        self.client.request('/api/schedules',{'name':'Schedule','interval_minutes':60,'config':self.config()})
        with server.connect() as db:
            db.execute("INSERT INTO integrations VALUES('telegram',?)",(json.dumps({'enabled':True,'token':server.encrypt(server.DB_PATH,'fake')}),))
        content=backup.create(server,'disposable-password-123')
        with tempfile.TemporaryDirectory() as temp:
            target=backup.restore(content,'disposable-password-123',Path(temp)/'restored')
            with sqlite3.connect(target/'harbor.db') as db:
                self.assertEqual(db.execute('SELECT enabled FROM webhooks').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT enabled FROM schedules').fetchone()[0],0)
                self.assertFalse(json.loads(db.execute('SELECT config FROM integrations').fetchone()[0])['enabled'])
