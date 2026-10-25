"""Playbooks use disposable data, loopback HTTP and fake model results only."""
import json
import os
os.environ["APP_MODE"]="mock"
os.environ["MOCK_DELAY"]="0"
import threading
import tempfile
import sqlite3
from pathlib import Path
import unittest
from app import server,playbooks,backup
import test_workspace as wh


class PlaybookTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    config=wh.WorkspaceTests.config
    project=wh.WorkspaceTests.project
    def setUp(self):
        with server.connect() as db:
            for table in ('playbook_attempts','playbook_steps','playbook_runs','playbooks'):db.execute('DELETE FROM '+table)
        wh.WorkspaceTests.setUp(self)
    def recipe(self,**kw):
        body=dict(name='Investigate',steps=[{'title':'Collect','config':self.config()},{'title':'Verify','config':self.config()}]);body.update(kw)
        code,r=self.client.request('/api/playbooks',body);self.assertEqual(code,201,r);return r['id']
    def start(self,rid):
        code,r=self.client.request('/api/playbooks/start',{'id':rid,'input':'Original evidence'});self.assertEqual(code,201,r);return r['id']
    def detail(self,rid):
        code,r=self.client.request('/api/playbooks/runs?id='+rid);self.assertEqual(code,200,r);return r
    def advance(self,rid,index=0,**extra):return self.client.request('/api/playbooks/runs/advance',dict(id=rid,expected_step=index,**extra))
    def finish(self,tid,output='Evidence result',status='completed'):
        with server.connect() as db:db.execute('UPDATE tasks SET status=?,synthesis=? WHERE id=?',(status,output,tid))
    def test_creation_start_and_reads_never_dispatch(self):
        rid=self.start(self.recipe());d=self.detail(rid);self.assertEqual(d['status'],'ready')
        for _ in range(3):self.client.request('/api/playbooks/runs');self.detail(rid)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def test_each_step_needs_fresh_approval_and_preserves_full_evidence(self):
        rid=self.start(self.recipe());code,r=self.advance(rid);self.assertEqual(code,200,r);tid=r['task_id']
        self.assertEqual(self.advance(rid)[0],400)
        long_output='long evidence\n'*20000;self.finish(tid,long_output)
        d=self.detail(rid);self.assertEqual(d['status'],'ready');self.assertEqual(d['next_step'],1)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        self.assertEqual(self.advance(rid,0)[0],400)
        code,r=self.advance(rid,1);self.assertEqual(code,200,r)
        with server.connect() as db:
            context=db.execute('SELECT continuation_context FROM tasks WHERE id=?',(r['task_id'],)).fetchone()[0]
            self.assertIn(long_output,context);self.assertIn('Original evidence',context)
        self.finish(r['task_id']);self.assertEqual(self.detail(rid)['status'],'completed');self.assertEqual(self.advance(rid,2)[0],400)
    def test_failed_step_requires_exact_explicit_retry_and_retains_attempt(self):
        rid=self.start(self.recipe());_,r=self.advance(rid);tid=r['task_id'];self.finish(tid,'Partial evidence','incomplete')
        self.assertEqual(self.detail(rid)['status'],'needs_attention');self.assertEqual(self.advance(rid)[0],400)
        self.assertEqual(self.advance(rid,retry_task='wrong')[0],400)
        code,r=self.advance(rid,retry_task=tid);self.assertEqual(code,200,r);self.assertNotEqual(r['task_id'],tid)
        d=self.detail(rid);self.assertEqual(d['steps'][0]['attempt'],2);self.assertEqual(d['attempts'][0]['output'],'Partial evidence')
        self.assertEqual(self.advance(rid,retry_task=tid)[0],400)
    def test_pause_resume_do_not_start_or_repeat_active_step(self):
        rid=self.start(self.recipe());_,r=self.advance(rid);tid=r['task_id']
        self.client.request('/api/playbooks/runs/pause',{'id':rid});self.assertEqual(self.advance(rid)[0],400)
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'queued')
        self.finish(tid);self.assertEqual(self.detail(rid)['status'],'paused')
        self.client.request('/api/playbooks/runs/resume',{'id':rid});self.assertEqual(self.detail(rid)['status'],'ready')
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
    def test_cancel_stops_pending_work_and_never_resumes(self):
        rid=self.start(self.recipe());_,r=self.advance(rid)
        self.assertEqual(self.client.request('/api/playbooks/runs/cancel',{'id':rid})[0],200)
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(r['task_id'],)).fetchone()[0],'cancelled')
        self.assertEqual(self.detail(rid)['status'],'cancelled');self.assertEqual(self.advance(rid)[0],400)
        self.assertEqual(self.client.request('/api/playbooks/runs/resume',{'id':rid})[0],400)
    def test_history_deletion_preserves_recipe_copy_until_separately_removed(self):
        rid=self.start(self.recipe());_,r=self.advance(rid);tid=r['task_id'];self.finish(tid,'Keep full result')
        server.delete_history(self.user,{'ids':[tid]})
        d=self.detail(rid);self.assertFalse(d['steps'][0]['task_exists']);self.assertEqual(d['steps'][0]['output'],'Keep full result');self.assertEqual(d['next_step'],1)
        self.assertEqual(self.client.request('/api/playbooks/runs/pause',{'id':rid})[0],200)
        self.assertEqual(self.client.request('/api/playbooks/runs/delete',{'id':rid})[0],200)
        self.assertEqual(self.client.request('/api/playbooks/runs?id='+rid)[0],400)
    def test_definition_snapshot_and_disabled_recipe(self):
        definition=self.recipe();rid=self.start(definition)
        self.client.request('/api/playbooks/toggle',{'id':definition,'enabled':False})
        self.assertEqual(self.client.request('/api/playbooks/start',{'id':definition,'input':'x'})[0],400)
        self.client.request('/api/playbooks/delete',{'id':definition})
        self.assertEqual(self.detail(rid)['steps'][0]['title'],'Collect');self.assertEqual(self.advance(rid)[0],200)
    def test_shared_read_but_owner_write_and_viewer_denied(self):
        definition=self.recipe();rid=self.start(definition)
        self.client.login('operator');self.assertEqual(self.client.request('/api/playbooks/runs?id='+rid)[0],200)
        self.assertEqual(self.advance(rid)[0],403);self.assertEqual(self.client.request('/api/playbooks/delete',{'id':definition})[0],403)
        own=self.start(definition);self.assertEqual(self.detail(own)['owner_id'],'operator')
        self.client.login('viewer');self.assertEqual(self.advance(own)[0],403)
        self.assertEqual(self.client.request('/api/playbooks/start',{'id':definition,'input':'x'})[0],403)
    def test_project_scope_and_invalid_definition_fail_atomically(self):
        pid=self.project();definition=self.recipe(project_id=pid);rid=self.start(definition)
        _,general=self.client.request('/api/playbooks/runs');self.assertEqual(general['items'],[])
        _,scoped=self.client.request('/api/playbooks/runs?project_id='+pid);self.assertEqual(scoped['items'][0]['id'],rid)
        code,_=self.client.request('/api/playbooks',{'name':'bad','steps':[{'title':'x','config':{'prompt':'x','agents':['unknown']}}]});self.assertEqual(code,400)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM playbooks').fetchone()[0],1)
    def test_concurrent_double_click_only_creates_one_task(self):
        rid=self.start(self.recipe());results=[]
        def start():
            try:results.append(playbooks.mutate(server,self.user,'/api/playbooks/runs/advance',{'id':rid,'expected_step':0}))
            except ValueError:results.append(None)
        threads=[threading.Thread(target=start) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(5);self.assertFalse(t.is_alive())
        self.assertEqual(sum(r is not None for r in results),1)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
    def test_restart_never_replays_interrupted_step(self):
        rid=self.start(self.recipe());_,r=self.advance(rid)
        with server.connect() as db:db.execute("UPDATE tasks SET status='running' WHERE id=?",(r['task_id'],))
        server.initialize();self.assertEqual(self.detail(rid)['status'],'needs_attention')
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        self.assertEqual(self.advance(rid)[0],400)

    def test_revoked_mcp_grant_blocks_dispatch_without_orphan_task(self):
        definition=self.recipe(steps=[{'title':'Read tools','config':self.config(mcp_servers=['a'*32])}])
        rid=self.start(definition)
        code,_=self.advance(rid);self.assertEqual(code,400)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT attempt FROM playbook_steps WHERE run_id=?',(rid,)).fetchone()[0],0)

    def test_backup_restore_pauses_runs_and_preserves_snapshot(self):
        rid=self.start(self.recipe())
        content=backup.create(server,'disposable-restore-pass')
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'restored';backup.restore(content,'disposable-restore-pass',dest)
            with sqlite3.connect(dest/'harbor.db') as db:
                self.assertEqual(db.execute('SELECT control FROM playbook_runs WHERE id=?',(rid,)).fetchone()[0],'paused')
                self.assertEqual(db.execute('SELECT count(*) FROM playbook_steps WHERE run_id=?',(rid,)).fetchone()[0],2)
                self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
