import os
os.environ['APP_MODE']='mock'
import json
import unittest
from app import server,lab
import test_workspace as wh

class LabTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    config=wh.WorkspaceTests.config
    def setUp(self):
        with server.connect() as db:db.execute('DELETE FROM lab_entries');db.execute('DELETE FROM lab_trials')
        wh.WorkspaceTests.setUp(self)
    def trial(self):
        code,d=self.client.request('/api/lab',{'name':'Compare','agents':['local','research'],'config':self.config(mask_sensitive=True,web_enabled=True,mcp_servers=['disabled'])});self.assertEqual(code,201,d);return d['id']
    def detail(self,ident):return self.client.request('/api/lab?id='+ident)[1]
    def finish(self,ident):
        for entry in self.detail(ident)['entries']:
            with server.connect() as db:db.execute("UPDATE tasks SET status='completed',synthesis=?,cost=.25 WHERE id=?",('Answer '+entry['label'],entry['task_id']))
    def test_equal_inputs_and_disabled_external_tools(self):
        ident=self.trial();d=self.detail(ident);self.assertEqual([e['label'] for e in d['entries']],['A','B'])
        with server.connect() as db:
            tasks=db.execute('SELECT prompt,web_enabled,mcp_selection,mask_sensitive,fallback_agent,privacy_seed FROM tasks').fetchall()
            self.assertEqual(len(tasks),2);self.assertEqual(len({t['privacy_seed'] for t in tasks}),1);self.assertTrue(tasks[0]['privacy_seed'])
            for t in tasks:self.assertEqual(t['prompt'],'Kanıtları incele');self.assertFalse(t['web_enabled']);self.assertEqual(t['mcp_selection'],'[]');self.assertEqual(t['mask_sensitive'],1);self.assertIsNone(t['fallback_agent'])
        self.assertIsNone(d['judge_task'])
    def test_judge_is_explicit_and_requires_successful_outputs(self):
        ident=self.trial();self.assertEqual(self.client.request('/api/lab/judge',{'id':ident,'agent':'critic'})[0],400)
        self.finish(ident);code,r=self.client.request('/api/lab/judge',{'id':ident,'agent':'critic'});self.assertEqual(code,200,r)
        with server.connect() as db:
            row=db.execute('SELECT * FROM tasks WHERE id=?',(r['task_id'],)).fetchone();self.assertIn('OUTPUT A',row['continuation_context']);self.assertIn('Answer B',row['continuation_context']);self.assertEqual(row['mask_sensitive'],1)
        self.assertEqual(self.client.request('/api/lab/judge',{'id':ident,'agent':'critic'})[0],400)
    def test_ratings_are_owner_only_and_not_accepted_while_running(self):
        ident=self.trial();entry=self.detail(ident)['entries'][0];body={'id':entry['id'],'rating':{'accuracy':4,'evidence':3,'usefulness':5},'notes':'Human opinion'}
        self.assertEqual(self.client.request('/api/lab/rate',body)[0],400);self.finish(ident)
        self.client.login('operator');self.assertEqual(self.client.request('/api/lab/rate',body)[0],403)
        self.client.login('admin');self.assertEqual(self.client.request('/api/lab/rate',body)[0],200)
        self.assertEqual(self.detail(ident)['entries'][0]['rating']['accuracy'],4)
        self.assertEqual(self.client.request('/api/lab/rate',dict(body,rating={'accuracy':6,'evidence':3,'usefulness':5}))[0],400)
    def test_history_deletion_retains_trial_output_and_metrics(self):
        ident=self.trial();self.finish(ident);entry=self.detail(ident)['entries'][0]
        server.delete_history(self.user,{'ids':[entry['task_id']]});d=self.detail(ident)
        self.assertEqual(d['entries'][0]['output'],'Answer A');self.assertEqual(d['entries'][0]['cost'],.25)
    def test_invalid_batch_rolls_back_all_tasks(self):
        code,_=self.client.request('/api/lab',{'name':'Invalid','agents':['local','unknown'],'config':self.config()});self.assertEqual(code,400)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],0);self.assertEqual(db.execute('SELECT count(*) FROM lab_trials').fetchone()[0],0)
    def test_viewer_cannot_launch_and_get_never_dispatches(self):
        self.client.request('/api/lab');self.client.login('viewer')
        self.assertEqual(self.client.request('/api/lab',{'name':'No','agents':['local','research'],'config':self.config()})[0],403)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
