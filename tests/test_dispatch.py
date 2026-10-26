"""Durable maintenance pause protects admission/claims without replaying or losing jobs."""
import os
os.environ['APP_MODE']='mock'
os.environ['MOCK_DELAY']='0'
import concurrent.futures
import time
import unittest
from app import server,operations,automation
import test_workspace as wh


class DispatchTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    config=wh.WorkspaceTests.config
    def setUp(self):
        wh.WorkspaceTests.setUp(self)
        with server.connect() as db:db.execute("UPDATE dispatch_control SET paused=0 WHERE id=1")
    def control(self,paused,expected):return self.client.request('/api/operations/dispatch',{'paused':paused,'expected_paused':expected})
    def task(self):return server.create_task(self.user,self.config())

    def test_only_admin_can_pause_and_boolean_values_are_strict(self):
        for role in ('operator','viewer'):
            self.client.login(role);self.assertEqual(self.control(True,False)[0],403)
        self.client.login('admin')
        for body in ({'paused':'true','expected_paused':False},{'paused':True,'expected_paused':'false'},{'paused':True}):self.assertEqual(self.client.request('/api/operations/dispatch',body)[0],400)
        self.assertEqual(self.control(True,False)[0],200)

    def test_stale_updates_are_rejected_and_idempotent_requests_do_not_duplicate_audit(self):
        with server.connect() as db:previous=db.execute("SELECT count(*) FROM audit_events WHERE event='dispatch_paused'").fetchone()[0]
        self.assertEqual(self.control(True,False)[0],200);self.assertEqual(self.control(False,False)[0],400)
        self.assertEqual(self.control(True,True)[0],200)
        with server.connect() as db:self.assertEqual(db.execute("SELECT count(*) FROM audit_events WHERE event='dispatch_paused'").fetchone()[0],previous+1)

    def test_pause_persists_across_reinitialization_and_keeps_http_ready(self):
        self.assertEqual(self.control(True,False)[0],200)
        with server.connect() as db:operations.initialize(db)
        _,state=self.client.request('/api/operations');self.assertTrue(state['dispatch']['paused']);self.assertTrue(state['dispatch']['drained'])
        self.assertEqual(self.client.request('/api/health/ready')[0],200)

    def test_new_jobs_are_rejected_without_creating_tasks_or_lab_entries(self):
        self.control(True,False)
        for path,body in [('/api/tasks',self.config()),('/api/lab',{'name':'Paused trial','agents':['local','research'],'config':self.config()})]:
            code,data=self.client.request(path,body);self.assertEqual(code,503,data);self.assertEqual(data['code'],'dispatch_paused')
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM lab_trials').fetchone()[0],0)

    def test_queued_job_survives_pause_and_can_only_be_claimed_once(self):
        tid=self.task();self.control(True,False);self.assertIsNone(server.claim_task())
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'queued')
        self.control(False,True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(lambda _:server.claim_task(),range(2)))
        self.assertEqual(claims.count(tid),1);self.assertEqual(claims.count(None),1)

    def test_inflight_work_finishes_and_drain_state_waits_for_it(self):
        tid=self.task();self.assertEqual(server.claim_task(),tid);self.control(True,False)
        _,state=self.client.request('/api/operations');self.assertFalse(state['dispatch']['drained'])
        server.process_task(tid)
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'completed')
        _,state=self.client.request('/api/operations');self.assertTrue(state['dispatch']['drained'])

    def test_paused_schedules_stay_enabled_and_resume_without_catchup_burst(self):
        code,schedule=self.client.request('/api/schedules',{'name':'Maintenance fixture','config':self.config(),'interval_minutes':10});self.assertEqual(code,201,schedule)
        with server.connect() as db:previous=db.execute('SELECT next_run FROM schedules WHERE id=?',(schedule['id'],)).fetchone()[0]
        stamp=previous+86400;self.control(True,False);automation.schedule_tick(server,stamp)
        with server.connect() as db:
            row=db.execute('SELECT enabled,next_run,last_task FROM schedules WHERE id=?',(schedule['id'],)).fetchone();self.assertEqual(tuple(row),(1,previous,None))
        self.control(False,True);automation.schedule_tick(server,stamp);automation.schedule_tick(server,stamp)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
