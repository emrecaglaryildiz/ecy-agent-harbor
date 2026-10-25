"""MCP fixtures are loopback-only protocol servers with no actual external tools."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from app import server,mcp_registry,mcp_client
import test_workspace as wh


class MCPTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    config=wh.WorkspaceTests.config
    def setUp(self):
        wh.WorkspaceTests.setUp(self)
        with server.connect() as db:db.execute('DELETE FROM mcp_calls');db.execute('DELETE FROM mcp_servers')
        self.calls=[];calls=self.calls
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(inner):
                item=json.loads(inner.rfile.read(int(inner.headers['Content-Length'])));calls.append(item)
                method=item['method'];result={}
                if method=='initialize':result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}
                if method=='tools/list':result={'tools':[{'name':'lookup','description':'Read test data','inputSchema':{'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False},'annotations':{'readOnlyHint':True}}]}
                if method=='tools/call':result={'content':[{'type':'text','text':'answer:'+item['params']['arguments']['query']}]}
                if 'id' not in item:inner.send_response(202);inner.end_headers();return
                body=json.dumps({'jsonrpc':'2.0','id':item['id'],'result':result}).encode();inner.send_response(200);inner.send_header('Content-Type','application/json');inner.send_header('Mcp-Session-Id','test-session');inner.send_header('Content-Length',str(len(body)));inner.end_headers();inner.wfile.write(body)
        self.fixture=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=self.fixture.serve_forever,daemon=True).start();self.addCleanup(self.fixture.server_close);self.addCleanup(self.fixture.shutdown)
    def connect(self):
        code,d=self.client.request('/api/mcp',{'name':'Fixture MCP','url':'http://127.0.0.1:'+str(self.fixture.server_port)+'/mcp','allow_private':True,'token':'fake-local-mcp-token'});self.assertEqual(code,201,d)
        code,_=self.client.request('/api/mcp/discover',{'id':d['id']});self.assertEqual(code,200)
        return d['id']
    def test_discovery_default_denied_and_secret_redacted(self):
        sid=self.connect();_,d=self.client.request('/api/mcp');row=d['servers'][0]
        self.assertFalse(row['enabled']);self.assertEqual(row['tools'][0]['policy'],'blocked');self.assertTrue(row['tools'][0]['declared_read_only']);self.assertNotIn('fake-local-mcp-token',json.dumps(d))
        code,_=self.client.request('/api/mcp/call',{'id':sid,'tool':'lookup','arguments':{'query':'test'}});self.assertEqual(code,400)
    def test_manual_call_is_reviewed_once_and_argument_bound(self):
        sid=self.connect();self.client.request('/api/mcp/policy',{'id':sid,'enabled':True,'policies':{'lookup':'manual'}})
        _,call=self.client.request('/api/mcp/call',{'id':sid,'tool':'lookup','arguments':{'query':'immutable'}})
        self.assertFalse(any(c['method']=='tools/call' for c in self.calls))
        code,result=self.client.request('/api/mcp/approve',{'id':call['id'],'arguments':{'query':'tampered'}});self.assertEqual(code,200,result);self.assertEqual(result['status'],'completed')
        self.assertEqual(next(c for c in self.calls if c['method']=='tools/call')['params']['arguments']['query'],'immutable')
        self.assertEqual(self.client.request('/api/mcp/approve',{'id':call['id']})[0],400)
    def test_revocation_invalidates_pending_call(self):
        sid=self.connect();self.client.request('/api/mcp/policy',{'id':sid,'enabled':True,'policies':{'lookup':'manual'}})
        _,call=self.client.request('/api/mcp/call',{'id':sid,'tool':'lookup','arguments':{'query':'test'}})
        self.client.request('/api/mcp/disable',{'id':sid})
        self.assertEqual(self.client.request('/api/mcp/approve',{'id':call['id']})[0],400)
        self.assertFalse(any(c['method']=='tools/call' for c in self.calls))
    def test_task_tool_router_and_schema_denial(self):
        sid=self.connect();self.client.request('/api/mcp/policy',{'id':sid,'enabled':True,'policies':{'lookup':'task_read'}})
        code,t=self.client.request('/api/tasks',self.config(mcp_servers=[sid]));self.assertEqual(code,201,t)
        with server.connect() as db:task=dict(db.execute('SELECT * FROM tasks WHERE id=?',(t['id'],)).fetchone())
        router=mcp_registry.Router(server,task);name=router.descriptors[0]['function']['name']
        self.assertIn('answer:test',router.call(name,{'query':'test'}))
        with self.assertRaises(Exception):router.call(name,{'unknown':1})
        with self.assertRaises(PermissionError):router.call('web_open',{'url':'http://127.0.0.1'})
        self.client.request('/api/mcp/disable',{'id':sid})
        with self.assertRaises(ValueError):router.call(name,{'query':'test'})
    def test_unsafe_schema_and_destinations_fail_closed(self):
        for schema in ({'type':'object','$ref':'https://attacker.example/schema'},{'type':'object','properties':{'q':{'type':'string','pattern':'(a+)+$'}}}):
            with self.assertRaises(ValueError):mcp_registry.valid_tools([{'name':'x','inputSchema':schema}])
        for url,private in [('http://127.0.0.1/mcp',False),('http://169.254.169.254/mcp',True),('http://127.0.0.1/mcp?token=secret',True)]:
            with self.assertRaises(ValueError):mcp_client.endpoint(url,private)
        self.client.login('operator');self.assertEqual(self.client.request('/api/mcp/discover',{'id':'a'*32})[0],403)
    def test_stdio_requires_local_manifest_and_clean_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            script=Path(temp)/'fixture.py';script.write_text('import json,sys,os\nfor line in sys.stdin:\n r=json.loads(line)\n if "id" not in r:continue\n out={"protocolVersion":"2025-11-25"} if r["method"]=="initialize" else {"tools":[]}\n print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":out}),flush=True)\n')
            profile=Path(temp)/'profiles.json';profile.write_text(json.dumps({'fixture':{'command':sys.executable,'args':[str(script)]}}));profile.chmod(0o600)
            with patch.dict(os.environ,{'MCP_STDIO_MANIFEST':str(profile),'FAKE_PROVIDER_KEY':'must-not-inherit'}):
                client=mcp_client.Client({'transport':'stdio','profile':'fixture','timeout':3});self.assertEqual(client.tools(),[]);client.close();self.assertIsNotNone(client.process.poll())

    def test_permission_rechecked_after_slow_discovery(self):
        sid=self.connect();self.client.request('/api/mcp/policy',{'id':sid,'enabled':True,'policies':{'lookup':'manual'}})
        _,call=self.client.request('/api/mcp/call',{'id':sid,'tool':'lookup','arguments':{'query':'test'}})
        entered=threading.Event();release=threading.Event();called=[];result=[]
        tool={'name':'lookup','description':'Read test data','inputSchema':{'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False}}
        class Slow:
            def tools(self):entered.set();release.wait(3);return [tool]
            def call(self,*args):called.append(args);return {}
            def close(self):pass
        with patch.object(mcp_registry,'connection',return_value=Slow()):
            worker=threading.Thread(target=lambda:result.append(self.client.request('/api/mcp/approve',{'id':call['id']})));worker.start()
            self.assertTrue(entered.wait(2));self.client.request('/api/mcp/disable',{'id':sid});release.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(called,[]);self.assertEqual(result[0][1]['status'],'failed')

    def test_stdio_stalled_input_is_killed_within_deadline(self):
        import subprocess,queue,time
        client=mcp_client.Client.__new__(mcp_client.Client);client.closed=False;client.sequence=0;client.timeout=.2;client.config={'transport':'stdio'};client.responses=queue.Queue()
        client.process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            import fcntl
            if hasattr(fcntl,'F_SETPIPE_SZ'):fcntl.fcntl(client.process.stdin.fileno(),fcntl.F_SETPIPE_SZ,4096)
            stamp=time.monotonic()
            with self.assertRaises(TimeoutError):client.rpc('tools/call',{'name':'stalled','arguments':{'query':'x'*(2*1024*1024)}})
            self.assertLess(time.monotonic()-stamp,5);self.assertIsNotNone(client.process.poll())
        finally:client.close()

    def test_retry_preserves_mcp_selection(self):
        sid=self.connect();self.client.request('/api/mcp/policy',{'id':sid,'enabled':True,'policies':{'lookup':'task_read'}})
        _,task=self.client.request('/api/tasks',self.config(mcp_servers=[sid]))
        with server.connect() as db:
            db.execute("UPDATE tasks SET status='failed' WHERE id=?",(task['id'],))
            db.execute("INSERT INTO runs(id,task_id,agent_id,kind,status,created_at,request_state) VALUES('failed-mcp-run',?,'local','participant','failed',?,?)",(task['id'],server.now(),json.dumps({'prompt':'Retry','context':''})))
        code,new=self.client.request('/api/tasks/'+task['id']+'/retry',{'run_id':'failed-mcp-run'});self.assertEqual(code,201,new)
        with server.connect() as db:selection=json.loads(db.execute('SELECT mcp_selection FROM tasks WHERE id=?',(new['id'],)).fetchone()[0])
        self.assertEqual(selection[0]['id'],sid)
