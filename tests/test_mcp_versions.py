"""Governance tests use the loopback MCP fixture; no real provider or MCP calls."""
import os
os.environ['APP_MODE']='mock'
import json
import threading
import unittest
from unittest.mock import patch
from app import server,mcp_registry,mcp_versions
import test_mcp as mh


class MCPVersionTests(unittest.TestCase):
    setUpClass=classmethod(mh.MCPTests.setUpClass.__func__)
    tearDownClass=classmethod(mh.MCPTests.tearDownClass.__func__)
    setUp=mh.MCPTests.setUp
    connect=mh.MCPTests.connect
    config=mh.MCPTests.config
    def latest(self,sid):
        with server.connect() as db:return db.execute('SELECT * FROM mcp_servers WHERE id=?',(sid,)).fetchone()

    def test_history_omits_sensitive_configuration_and_call_contents(self):
        sid=self.connect();row=self.latest(sid)
        self.client.request('/api/mcp/policy',{'id':sid,'expected_revision':row['revision'],'enabled':True,'policies':{'lookup':'manual'}})
        _,call=self.client.request('/api/mcp/call',{'id':sid,'tool':'lookup','arguments':{'query':'private-question'}})
        self.client.request('/api/mcp/disable',{'id':sid})
        code,h=self.client.request('/api/mcp/history?server_id='+sid);self.assertEqual(code,200,h)
        self.assertEqual([r['reason'] for r in h['items']],['disabled','policy','discovery','connection'])
        self.assertEqual([r['revision'] for r in h['items']],[4,3,2,1])
        encoded=json.dumps(h)
        for private in ('fake-local-mcp-token','127.0.0.1','private-question','inputSchema','Read test data','encrypted_token'):
            self.assertNotIn(private,encoded)
        self.assertEqual(len(h['items'][0]['tools'][0]['schema_hash']),64)
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM mcp_calls WHERE id=?',(call['id'],)).fetchone()[0],'rejected')

    def test_stale_form_rejected_without_overwriting_policy(self):
        sid=self.connect();old=self.latest(sid)['revision']
        self.client.request('/api/mcp/policy',{'id':sid,'expected_revision':old,'enabled':True,'policies':{'lookup':'manual'}})
        for path in ('/api/mcp/policy','/api/mcp/discover','/api/mcp/disable','/api/mcp'):
            code,_=self.client.request(path,{'id':sid,'expected_revision':old,'name':'stale','url':'http://127.0.0.1:9/mcp','allow_private':True,'enabled':True,'policies':{'lookup':'task_read'}})
            self.assertEqual(code,400,path)
        self.assertEqual(self.latest(sid)['revision'],old+1)
        self.assertEqual(json.loads(self.latest(sid)['tools'])[0]['policy'],'manual')
        for invalid in (True,'3',-1):self.assertEqual(self.client.request('/api/mcp/disable',{'id':sid,'expected_revision':invalid})[0],400)

    def test_changed_schema_description_or_read_only_hint_resets_permission(self):
        raw={'name':'lookup','description':'Read test data','inputSchema':{'type':'object'},'annotations':{'readOnlyHint':True}}
        old=mcp_registry.valid_tools([raw]);old[0]['policy']='task_read'
        self.assertEqual(mcp_registry.valid_tools([raw],old)[0]['policy'],'task_read')
        for changed in (dict(raw,inputSchema={'type':'object','required':['x']}),dict(raw,description='changed'),dict(raw,annotations={'readOnlyHint':False})):
            self.assertEqual(mcp_registry.valid_tools([changed],old)[0]['policy'],'blocked')

    def test_live_annotation_drift_prevents_external_call(self):
        sid=self.connect();self.client.request('/api/mcp/policy',{'id':sid,'enabled':True,'policies':{'lookup':'manual'}})
        _,pending=self.client.request('/api/mcp/call',{'id':sid,'tool':'lookup','arguments':{'query':'test'}})
        called=[];tool=json.loads(self.latest(sid)['tools'])[0]
        class Changed:
            def tools(self):return [{'name':tool['name'],'description':tool['description'],'inputSchema':tool['inputSchema'],'annotations':{'readOnlyHint':False}}]
            def call(self,*args):called.append(args);return {}
            def close(self):pass
        with patch.object(mcp_registry,'connection',return_value=Changed()):code,result=self.client.request('/api/mcp/approve',{'id':pending['id']})
        self.assertEqual(code,200);self.assertEqual(result['status'],'failed');self.assertEqual(called,[])

    def test_slow_discovery_cannot_overwrite_newer_revocation(self):
        sid=self.connect();entered=threading.Event();release=threading.Event();result=[]
        class Slow:
            def tools(self):entered.set();release.wait(3);return []
            def close(self):pass
        with patch.object(mcp_registry,'connection',return_value=Slow()):
            thread=threading.Thread(target=lambda:result.append(self.client.request('/api/mcp/discover',{'id':sid})));thread.start()
            self.assertTrue(entered.wait(2));self.client.request('/api/mcp/disable',{'id':sid});after=self.latest(sid);release.set();thread.join(4)
        self.assertFalse(thread.is_alive());self.assertEqual(result[0][0],400)
        self.assertEqual(self.latest(sid)['revision'],after['revision']);self.assertIsNone(self.latest(sid)['error'])

    def test_baseline_migration_is_idempotent_and_history_paginated_admin_only(self):
        sid=self.connect()
        with server.connect() as db:
            db.execute('DELETE FROM mcp_versions WHERE server_id=?',(sid,));mcp_versions.initialize(db);mcp_versions.initialize(db)
            self.assertEqual(db.execute('SELECT count(*) FROM mcp_versions WHERE server_id=?',(sid,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT reason FROM mcp_versions WHERE server_id=?',(sid,)).fetchone()[0],'baseline')
        for _ in range(22):self.assertEqual(self.client.request('/api/mcp/disable',{'id':sid})[0],200)
        _,h=self.client.request('/api/mcp/history?server_id='+sid);self.assertEqual(len(h['items']),20)
        _,last=self.client.request('/api/mcp/history?server_id='+sid+'&before='+str(h['next_before']));self.assertEqual(len(last['items']),3)
        self.assertEqual(self.client.request('/api/mcp/history?server_id='+sid+'&before=0')[0],400)
        for role in ('operator','viewer'):
            self.client.login(role);self.assertEqual(self.client.request('/api/mcp/history?server_id='+sid)[0],403)
