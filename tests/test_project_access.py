"""Project boundaries use real HTTP/SQLite and disposable accounts; no provider calls."""
import base64
import os
os.environ['APP_MODE']='mock'
os.environ['MOCK_DELAY']='0'
import json
import uuid
import unittest
from unittest.mock import patch
from app import server,access,automation
import test_workspace as wh
import test_harbor as helpers


class ProjectAccessTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    config=wh.WorkspaceTests.config
    upload=wh.WorkspaceTests.upload
    def setUp(self):
        wh.WorkspaceTests.setUp(self)
        code,p=self.client.request('/api/projects',{'name':'Private evidence','instruction':'PRIVATE_INSTRUCTION'})
        self.assertEqual(code,201,p);self.pid=p['id']
        self.fid=self.upload('PRIVATE_EVIDENCE\n',project=self.pid)
        self.tid=server.create_task(self.user,self.config(project_id=self.pid,file_ids=[self.fid]))
        with server.connect() as db:
            db.execute("UPDATE tasks SET status='completed',synthesis='PRIVATE_REPORT',cost=7 WHERE id=?",(self.tid,))
            server.log(db,self.tid,'evidence','PRIVATE_EVENT')
        self.operator=helpers.Client();self.operator.login('operator')
        self.viewer=helpers.Client();self.viewer.login('viewer')

    def member(self,grant,user='operator'):
        code,r=self.client.request('/api/projects/members',{'project_id':self.pid,'user_id':user,'access':grant})
        self.assertEqual(code,200,r)

    def test_new_projects_are_private_and_lists_do_not_leak(self):
        _,own=self.client.request('/api/workspace');p=next(p for p in own['projects'] if p['id']==self.pid)
        self.assertEqual(p['visibility'],'private');self.assertTrue(p['can_manage'])
        for client in (self.operator,self.viewer):
            _,workspace=client.request('/api/workspace');self.assertFalse(any(p['id']==self.pid for p in workspace['projects']));self.assertFalse(any(f['id']==self.fid for f in workspace['files']))
            _,state=client.request('/api/state');self.assertNotIn(self.tid,json.dumps(state));self.assertEqual(state['usage']['total_cost'],0)
            _,activity=client.request('/api/platform/activity');self.assertNotIn('PRIVATE_',json.dumps(activity))
            _,ops=client.request('/api/operations');self.assertEqual(sum(ops['counts'].values()),0)

    def test_direct_read_export_search_thread_and_incident_paths_are_denied(self):
        paths=['/api/tasks/'+self.tid,'/api/tasks/'+self.tid+'/export?format=md','/api/tasks/'+self.tid+'/export?format=pdf','/api/files/'+self.fid,'/api/files/'+self.fid+'?image=1','/api/files/search?q=PRIVATE&project_id='+self.pid,'/api/platform/thread?task='+self.tid,'/api/platform/compare?ids='+self.tid+',other','/api/platform/findings?project_id='+self.pid,'/api/platform/knowledge?q=PRIVATE&project_id='+self.pid,'/api/incidents?project_id='+self.pid,'/api/incidents/log?project_id='+self.pid+'&file_id='+self.fid,'/api/playbooks?project_id='+self.pid,'/api/lab?project_id='+self.pid]
        for path in paths:
            code,data=self.operator.request(path);self.assertEqual(code,403,(path,data));self.assertNotIn('PRIVATE_',json.dumps(data))

    def test_read_membership_allows_read_but_not_execution_or_upload(self):
        self.member('read');self.assertEqual(self.operator.request('/api/tasks/'+self.tid)[0],200)
        cfg=self.config(project_id=self.pid)
        for path,body in [('/api/tasks',cfg),('/api/files',{'name':'new.log','project_id':self.pid,'data':base64.b64encode(b'new').decode()}),('/api/templates',{'name':'Copy','config':cfg}),('/api/context/preview',cfg),('/api/platform/knowledge',{'project_id':self.pid,'title':'A','content':'new'})]:
            self.assertEqual(self.operator.request(path,body)[0],403,path)

    def test_write_membership_and_global_viewer_intersection(self):
        self.member('write');self.assertEqual(self.operator.request('/api/tasks',self.config(project_id=self.pid))[0],201)
        self.member('write','viewer');self.assertEqual(self.viewer.request('/api/tasks/'+self.tid)[0],200)
        self.assertEqual(self.viewer.request('/api/tasks',self.config(project_id=self.pid))[0],403)

    def test_editor_cannot_grant_permissions_or_change_owner_project(self):
        self.member('write')
        self.assertEqual(self.operator.request('/api/projects/members?project_id='+self.pid)[0],403)
        self.assertEqual(self.operator.request('/api/projects/members',{'project_id':self.pid,'user_id':'viewer','access':'write'})[0],403)
        self.assertEqual(self.operator.request('/api/projects',{'id':self.pid,'name':'Hijack','instruction':''})[0],403)
        for user in ('admin',):self.assertEqual(self.client.request('/api/projects/members',{'project_id':self.pid,'user_id':user,'access':'none'})[0],400)

    def test_revocation_takes_effect_in_existing_session_and_cancels_pending_work(self):
        self.member('write');code,task=self.operator.request('/api/tasks',self.config(project_id=self.pid));self.assertEqual(code,201,task)
        self.member('none');self.assertEqual(self.operator.request('/api/tasks/'+self.tid)[0],403)
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(task['id'],)).fetchone()[0],'cancelled')
        with patch.object(server,'adapter_for',side_effect=AssertionError('must not call provider')):server.process_task(task['id'])

    def test_private_snapshots_in_lab_and_playbooks_are_denied_by_id(self):
        code,trial=self.client.request('/api/lab',{'name':'Private trial','config':self.config(project_id=self.pid),'agents':['local','research']});self.assertEqual(code,201,trial)
        code,pb=self.client.request('/api/playbooks',{'name':'Private flow','project_id':self.pid,'steps':[{'title':'Review','config':self.config()}]});self.assertEqual(code,201,pb)
        code,run=self.client.request('/api/playbooks/start',{'id':pb['id'],'input':'PRIVATE_INPUT'});self.assertEqual(code,201,run)
        for path in ('/api/lab?id='+trial['id'],'/api/playbooks/runs?id='+run['id']):self.assertEqual(self.operator.request(path)[0],403)
        for path,body in [('/api/lab/delete',{'id':trial['id']}),('/api/playbooks/start',{'id':pb['id'],'input':'Try'}),('/api/playbooks/runs/advance',{'id':run['id']})]:self.assertEqual(self.operator.request(path,body)[0],403)

    def test_templates_schedules_and_notes_hidden_after_revocation(self):
        self.member('write');cfg=self.config(project_id=self.pid)
        code,template=self.operator.request('/api/templates',{'name':'PRIVATE_TEMPLATE','config':cfg});self.assertEqual(code,201,template)
        code,schedule=self.operator.request('/api/schedules',{'name':'PRIVATE_SCHEDULE','config':cfg,'interval_minutes':10});self.assertEqual(code,201,schedule)
        code,note=self.operator.request('/api/platform/knowledge',{'project_id':self.pid,'title':'PRIVATE_NOTE','content':'Private note'});self.assertEqual(code,201,note)
        self.member('none');_,workspace=self.operator.request('/api/workspace');self.assertNotIn('PRIVATE_',json.dumps(workspace))
        for path,ident in [('/api/templates/delete',template['id']),('/api/schedules/toggle',schedule['id']),('/api/platform/knowledge/delete',note['id'])]:self.assertEqual(self.operator.request(path,{'id':ident})[0],403)
        automation.schedule_tick(server,timestamp=10**12)
        with server.connect() as db:self.assertEqual(db.execute('SELECT enabled FROM schedules WHERE id=?',(schedule['id'],)).fetchone()[0],0)

    def test_old_projects_keep_shared_scope_but_can_be_restricted(self):
        ident=uuid.uuid4().hex
        with server.connect() as db:db.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(ident,'Legacy','',self.user['id'],server.now()))
        _,data=self.operator.request('/api/workspace');self.assertTrue(any(p['id']==ident and p['visibility']=='workspace' for p in data['projects']))
        self.assertEqual(self.client.request('/api/projects',{'id':ident,'name':'Legacy','instruction':'','visibility':'private'})[0],201)
        _,data=self.operator.request('/api/workspace');self.assertFalse(any(p['id']==ident for p in data['projects']))
        with server.connect() as db:access.initialize(db);self.assertEqual(access.mode(db,ident),'private')

    def test_private_projects_are_excluded_from_shared_telegram_outbox(self):
        with server.connect() as db:db.execute("INSERT INTO integrations VALUES('telegram',?)",(json.dumps({'enabled':True,'enabled_since':'2000-01-01T00:00:00Z'}),))
        automation.enqueue_notifications(server)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM notifications WHERE task_id=?',(self.tid,)).fetchone()[0],0)

    def test_member_cannot_read_or_reuse_tasks_after_downgrade(self):
        self.member('write');code,task=self.operator.request('/api/tasks',self.config(project_id=self.pid));self.assertEqual(code,201,task)
        with server.connect() as db:db.execute("UPDATE tasks SET status='completed' WHERE id=?",(task['id'],))
        self.member('read')
        self.assertEqual(self.operator.request('/api/tasks/'+task['id'])[0],200)
        self.assertEqual(self.operator.request('/api/tasks/'+task['id']+'/followup',{'prompt':'Reuse'})[0],403)
        self.assertEqual(self.operator.request('/api/history/delete',{'ids':[task['id']]})[0],403)

    def test_revocation_rejects_pending_artifact_and_mcp_approvals(self):
        self.member('write');code,task=self.operator.request('/api/tasks',self.config(project_id=self.pid));self.assertEqual(code,201,task)
        with server.connect() as db:
            db.execute("UPDATE tasks SET status='awaiting_approval' WHERE id=?",(task['id'],))
            db.execute("INSERT INTO approvals VALUES('private-approval',?,'artifact.write_text','{}','pending',NULL,?,NULL)",(task['id'],server.now()))
            db.execute("INSERT INTO mcp_calls(id,server_id,tool,owner_id,task_id,arguments,status,revision,created_at,updated_at) VALUES('private-call','fixture','read','operator',?,'{}','approved',1,?,?)",(task['id'],server.now(),server.now()))
        self.member('none')
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM approvals WHERE id='private-approval'").fetchone()[0],'rejected')
            self.assertEqual(db.execute("SELECT status FROM mcp_calls WHERE id='private-call'").fetchone()[0],'rejected')
        with patch.object(server.mcp_registry,'connection',side_effect=AssertionError('must not dispatch')):
            with self.assertRaises(ValueError):server.mcp_registry.execute(server,'private-call')

    def test_queued_shared_notification_is_skipped_after_project_becomes_private(self):
        with server.connect() as db:
            db.execute("INSERT INTO integrations VALUES('telegram',?)",(json.dumps({'enabled':True,'token':'unused'}),))
            db.execute("INSERT INTO notifications VALUES('private-notification',?,'pending',NULL,?,NULL)",(self.tid,server.now()))
        class NeverSend:
            def open(self,*a,**kw):raise AssertionError('must not send private evidence')
        automation.send_one(server,NeverSend())
        with server.connect() as db:self.assertEqual(db.execute("SELECT status FROM notifications WHERE id='private-notification'").fetchone()[0],'skipped')
