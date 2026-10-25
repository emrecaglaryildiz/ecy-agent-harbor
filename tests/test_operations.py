import os
os.environ['APP_MODE']='mock'
import json
import time
import unittest
import urllib.request
from urllib.error import HTTPError,URLError
from unittest.mock import patch
from app import server,operations
import test_workspace as wh
import test_harbor as helpers

class OperationsTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    config=wh.WorkspaceTests.config
    def setUp(self):wh.WorkspaceTests.setUp(self)
    def enroll(self):
        code,r=self.client.request('/api/operations/nodes',{'name':'Fake GPU','hardware':'Test only'});self.assertEqual(code,200,r);return r
    def heartbeat(self,node,body,token=None):
        req=urllib.request.Request(helpers.BASE+node['endpoint'],data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+(token if token is not None else node['token'])})
        try:r=urllib.request.urlopen(req,timeout=3)
        except HTTPError as exc:r=exc
        return r.code,json.loads(r.read())
    def test_scoped_heartbeat_auth_and_revocation(self):
        node=self.enroll();self.assertEqual(self.heartbeat(node,{'status':'busy','running_jobs':2})[0],200)
        _,d=self.client.request('/api/operations');self.assertEqual(d['nodes'][0]['state'],'busy');self.assertNotIn(node['token'],json.dumps(d));self.assertNotIn('token_hash',json.dumps(d))
        self.assertEqual(self.heartbeat(node,{},'wrong')[0],403)
        self.client.request('/api/operations/nodes/toggle',{'id':node['id'],'enabled':False});self.assertEqual(self.heartbeat(node,{})[0],403)
    def test_metrics_reject_invalid_values_and_commands(self):
        n=self.enroll()
        for body in ({'running_jobs':-1},{'load_1m':float('nan')},{'memory_used_mb':20,'memory_total_mb':10},{'command':'run something'}):self.assertEqual(self.heartbeat(n,body)[0],400)
    def test_node_credentials_cannot_read_or_submit_tasks(self):
        n=self.enroll();req=urllib.request.Request(helpers.BASE+'/api/operations',headers={'Authorization':'Bearer '+n['token']})
        with self.assertRaises(HTTPError) as e:urllib.request.urlopen(req)
        self.assertEqual(e.exception.code,401)
        self.client.login('viewer');self.assertEqual(self.client.request('/api/operations/nodes',{'name':'No'})[0],403);self.assertEqual(self.client.request('/api/operations')[0],200)
    def test_no_heartbeat_means_unknown_and_stale_is_explicit(self):
        n=self.enroll();_,d=self.client.request('/api/operations');self.assertEqual(d['nodes'][0]['state'],'unknown')
        self.heartbeat(n,{})
        with server.connect() as db:db.execute('UPDATE nodes SET last_seen=? WHERE id=?',(time.time()-121,n['id']))
        _,d=self.client.request('/api/operations');self.assertEqual(d['nodes'][0]['state'],'stale')
    def test_circuit_threshold_single_probe_and_recovery(self):
        agent=server.BY_ID['local'];ident=operations.key(agent)
        with patch.object(server,'MODE','live'):
            for _ in range(3):operations.failure(server,ident)
            with self.assertRaises(operations.CircuitOpen):operations.admit(server,agent)
            with server.connect() as db:db.execute('UPDATE provider_circuits SET open_until=0 WHERE id=?',(ident,))
            self.assertEqual(operations.admit(server,agent),ident)
            with self.assertRaises(operations.CircuitOpen):operations.admit(server,agent)
            operations.success(server,ident);operations.release(server,ident);self.assertEqual(operations.admit(server,agent),ident)
    def test_probe_failure_reopens_and_restart_releases_ambiguous_probe(self):
        ident=operations.key(server.BY_ID['local'])
        for _ in range(3):operations.failure(server,ident)
        with server.connect() as db:db.execute('UPDATE provider_circuits SET open_until=0 WHERE id=?',(ident,))
        with patch.object(server,'MODE','live'):operations.admit(server,server.BY_ID['local'])
        with server.connect() as db:operations.initialize(db)
        with server.connect() as db:r=db.execute('SELECT * FROM provider_circuits WHERE id=?',(ident,)).fetchone();self.assertEqual(r['lease_until'],0);self.assertGreater(r['open_until'],time.time())
    def test_only_transient_errors_count(self):
        self.assertTrue(operations.transient(URLError('offline')));self.assertTrue(operations.transient(HTTPError('https://invalid',503,'fail',{},None)));self.assertFalse(operations.transient(HTTPError('https://invalid',400,'bad',{},None)));self.assertFalse(operations.transient(ValueError('content')))
    def test_open_circuit_skips_provider_without_losing_task_history(self):
        tid=server.create_task(self.user,self.config())
        with server.connect() as db:db.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,))
        ident=operations.key(server.BY_ID['local'])
        for _ in range(3):operations.failure(server,ident)
        with patch.object(server,'MODE','live'),patch.object(server,'adapter_for',side_effect=AssertionError('must not call')):self.assertIsNone(server.run_agent(tid,'local','Test'))
        with server.connect() as db:r=db.execute('SELECT * FROM runs WHERE task_id=?',(tid,)).fetchone();self.assertEqual(r['status'],'failed');self.assertIn('Operasyon',r['error'])

    def test_anonymous_heartbeat_is_rejected_before_body_read(self):
        n=self.enroll()
        with patch.object(server.security,'read_json_body',side_effect=AssertionError('must not read')):
            self.assertEqual(self.heartbeat(n,{},'bad')[0],403)
