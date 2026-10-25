"""Integration checks: real HTTP, SQLite, threads, adapters and approval boundary."""
from contextlib import contextmanager
import http.cookiejar
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

os.environ["APP_MODE"] = "mock"
os.environ["MOCK_DELAY"] = "0.01"
from app import server
from app.adapters import MockAdapter, OpenAICompatibleAdapter, HermesBridgeAdapter, Result, checked_result


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""

    def request(self, path, body=None, csrf=True, origin=None):
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-CSRF-Token"] = self.csrf
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            response = self.opener.open(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        return response.code, json.loads(response.read())

    def login(self, uid):
        code, _ = self.request("/api/login", {"user": uid})
        assert code == 200
        code, state = self.request("/api/state")
        self.csrf = state["csrf"]
        return state


BASE = ""


@contextmanager
def scripted_provider(responses):
    captured = []
    class Provider(server.BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            index = len(captured) - 1
            self.send_response(200 if index < len(responses) else 500)
            self.end_headers()
            self.wfile.write(json.dumps(responses[index] if index < len(responses) else {'error':'Unexpected extra call'}).encode())
        def log_message(self, *args):
            pass
    httpd = server.ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=httpd.serve_forever,daemon=True).start()
    try:
        yield f'http://127.0.0.1:{httpd.server_port}/v1',captured
    finally:
        httpd.shutdown();httpd.server_close()


def model_response(content, finish='stop', **fields):
    return {'choices':[{'message':dict(content=content,**fields),'finish_reason':finish}],
            'usage':{'prompt_tokens':10,'completion_tokens':5}}


class HarborTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global BASE
        cls.temp = tempfile.TemporaryDirectory()
        server.DB_PATH = str(Path(cls.temp.name) / "test.db")
        server.initialize()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        BASE = f"http://127.0.0.1:{cls.httpd.server_port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.temp.cleanup()

    def setUp(self):
        server.security.ATTEMPTS.clear()
        with server.connect() as db:
            db.execute("DELETE FROM provider_circuits");db.execute("DELETE FROM nodes")
            for name in ("logs", "approvals", "runs", "tasks", "deleted_costs"):
                db.execute("DELETE FROM " + name)
            db.execute("DELETE FROM credentials")
            db.execute("DELETE FROM agent_configs")
            for a in server.DEFAULT_AGENTS:
                db.execute("INSERT INTO agent_configs VALUES(?,?,?)", (a["id"], json.dumps(a), server.now()))
        server.reload_agents()
        self.client = Client()
        self.client.login("admin")

    def create(self, **kwargs):
        body = dict(prompt="Test planı hazırla", mode="council", agents=["hermes", "local"], judge="critic", budget=1)
        body.update(kwargs)
        code, data = self.client.request("/api/tasks", body)
        self.assertEqual(code, 201, data)
        return data["id"]

    def process(self, tid):
        with server.connect() as db:
            db.execute("UPDATE tasks SET status='running' WHERE id=?", (tid,))
        server.process_task(tid)
        return self.client.request("/api/tasks/" + tid)[1]

    def test_health_and_anonymous_boundary(self):
        c = Client()
        self.assertEqual(c.request("/api/health")[0], 200)
        self.assertEqual(c.request("/api/state")[0], 401)

    def test_parallel_agents_and_judge(self):
        data = self.process(self.create())
        self.assertEqual(data["task"]["status"], "completed")
        self.assertEqual(len(data["runs"]), 3)
        self.assertIn("Hermes", data["task"]["synthesis"])
        self.assertEqual(data["task"]["cost"], 0)
        self.assertEqual(data["task"]["reserved"], 0)
        self.assertTrue(data["logs"])

    def test_single_agent_no_judge(self):
        data = self.process(self.create(mode="single", agents=["hermes"]))
        self.assertEqual(len(data["runs"]), 1)
        self.assertIsNone(data["task"]["synthesis"])

    def test_tool_requires_admin_and_executes_once(self):
        data = self.process(self.create(tool_requested=True))
        self.assertEqual(data["task"]["status"], "awaiting_approval")
        aid = self.client.request("/api/state")[1]["approvals"][0]["id"]
        folder = Path(server.DB_PATH).parent / "artifacts"
        self.assertFalse((folder / (data["task"]["id"] + ".txt")).exists())
        operator = Client()
        operator.login("operator")
        self.assertEqual(operator.request("/api/approvals/" + aid, {"decision": "approve"})[0], 403)
        self.assertEqual(self.client.request("/api/approvals/" + aid, {"decision": "approve"})[0], 200)
        self.assertTrue((folder / (data["task"]["id"] + ".txt")).is_file())
        self.assertEqual(self.client.request("/api/approvals/" + aid, {"decision": "approve"})[0], 400)

    def test_rejection_does_not_execute(self):
        tid = self.create(tool_requested=True)
        self.process(tid)
        aid = self.client.request("/api/state")[1]["approvals"][0]["id"]
        self.assertEqual(self.client.request("/api/approvals/" + aid, {"decision": "reject"})[0], 200)
        self.assertFalse((Path(server.DB_PATH).parent / "artifacts" / (tid + ".txt")).exists())

    def test_viewer_is_readonly(self):
        self.client.login("viewer")
        self.assertEqual(self.client.request("/api/tasks", {"prompt": "hi"})[0], 403)
        self.assertEqual(self.client.request("/api/state")[0], 200)

    def test_operator_cannot_cancel_other_users_task(self):
        tid = self.create()
        self.client.login("operator")
        self.assertEqual(self.client.request(f"/api/tasks/{tid}/cancel", {})[0], 403)

    def test_csrf_and_cross_origin(self):
        self.assertEqual(self.client.request("/api/tasks", {}, csrf=False)[0], 403)
        self.assertEqual(self.client.request("/api/login", {"user": "admin"}, origin="https://evil.example")[0], 403)

    def test_invalid_fields_and_budget(self):
        for body in [dict(prompt=""), dict(prompt="ok", agents=["bad"]), dict(prompt="ok", mode="single", agents=["hermes", "local"])]:
            self.assertEqual(self.client.request("/api/tasks", body)[0], 400)

    def test_spending_does_not_block_new_tasks(self):
        old=self.create();self.process(old)
        with server.connect() as db:
            db.execute('UPDATE tasks SET cost=12345 WHERE id=?',(old,))
        with patch.dict(os.environ,{'DAILY_BUDGET_USD':'0.01'}):
            tid=self.create(budget=.000001)
            task=self.client.request('/api/tasks/'+tid)[1]['task']
            self.assertEqual(task['reserved'],0)
            state=self.client.request('/api/state')[1]
            self.assertIsNone(state['daily_limit'])
            self.assertEqual(state['usage']['cost'],12345)
            self.assertEqual(state['usage']['total_cost'],12345)

    def test_queued_cancel_prevents_calls(self):
        tid = self.create(tool_requested=True)
        self.assertEqual(self.client.request(f"/api/tasks/{tid}/cancel", {})[0], 200)
        data = self.client.request("/api/tasks/" + tid)[1]
        self.assertEqual(data["task"]["status"], "cancelled")
        self.assertEqual(data["task"]["reserved"], 0)
        self.assertEqual(data["runs"], [])

    def test_failure_is_redacted_and_preserves_runs(self):
        with patch.object(MockAdapter, "complete", side_effect=RuntimeError("secret-key-do-not-leak")):
            data = self.process(self.create())
        self.assertEqual(data["task"]["status"], "failed")
        self.assertNotIn("secret-key", json.dumps(data))
        self.assertEqual(len(data["runs"]), 2)

    def test_restart_does_not_replay_running_task(self):
        tid = self.create()
        with server.connect() as db:
            db.execute("UPDATE tasks SET status='running' WHERE id=?", (tid,))
        server.initialize()
        data = self.client.request("/api/tasks/" + tid)[1]
        self.assertEqual(data["task"]["status"], "failed")
        self.assertEqual(data["task"]["reserved"], 0)

    def test_real_worker_claims_queued_task(self):
        tid = self.create()
        server.STOP.clear()
        thread = threading.Thread(target=server.worker)
        thread.start()
        try:
            end = time.monotonic() + 3
            while time.monotonic() < end:
                data = self.client.request("/api/tasks/" + tid)[1]
                if data["task"]["status"] == "completed":
                    break
                time.sleep(.02)
            self.assertEqual(data["task"]["status"], "completed")
        finally:
            server.STOP.set()
            thread.join(5)

    def test_live_mode_requires_server_token(self):
        with patch.object(server, "MODE", "live"), patch.dict(os.environ, {"AUTH_ADMIN_TOKEN": "test-only-random-value"}):
            self.assertEqual(Client().request("/api/login", {"user": "admin"})[0], 403)
            self.assertEqual(Client().request("/api/login", {"user": "admin", "token": "test-only-random-value"})[0], 200)

    def test_participants_actually_overlap(self):
        barrier = threading.Barrier(2, timeout=2)
        def complete(agent, prompt, context=""):
            barrier.wait()
            return Result("concurrent output", 10, 10)
        with patch.object(MockAdapter, "complete", side_effect=complete):
            data = self.process(self.create(judge=None))
        self.assertEqual(data["task"]["status"], "completed")

    def test_cancellation_during_provider_call_keeps_cost_and_releases_reserve(self):
        started, finish = threading.Event(), threading.Event()
        def complete(agent, prompt, context=""):
            started.set()
            finish.wait(2)
            return Result("finished after cancellation", 10, 10)
        tid = self.create(mode="single", agents=["hermes"])
        with patch.object(MockAdapter, "complete", side_effect=complete):
            with server.connect() as db:
                db.execute("UPDATE tasks SET status='running' WHERE id=?", (tid,))
            thread = threading.Thread(target=server.process_task, args=(tid,))
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertEqual(self.client.request(f"/api/tasks/{tid}/cancel", {})[0], 200)
            finish.set()
            thread.join(3)
        data = self.client.request("/api/tasks/" + tid)[1]
        self.assertEqual(data["task"]["status"], "cancelled")
        self.assertEqual(data["task"]["reserved"], 0)
        self.assertEqual(len(data["runs"]), 1)

    def test_hermes_bridge_is_opt_in(self):
        with patch.dict(os.environ, {"HERMES_BRIDGE_SAFE_MODE": "0"}):
            with self.assertRaises(RuntimeError):
                HermesBridgeAdapter().complete(server.BY_ID["hermes"], "test")

    def test_negative_usage_is_rejected(self):
        with self.assertRaises(ValueError):
            checked_result("output", -20, 100)

    def test_live_failure_preserves_conservative_cost(self):
        tid = self.create(mode="single", agents=["hermes"])
        with patch.object(server, "MODE", "live"), patch.object(server, "adapter_for", return_value=MockAdapter()), patch.object(MockAdapter, "complete", side_effect=RuntimeError("timeout")):
            data = self.process(tid)
        self.assertEqual(data["task"]["status"], "failed")
        self.assertAlmostEqual(data["task"]["cost"], server.BY_ID["hermes"]["price"] * 21.5)

    def test_openai_compatible_http_contract(self):
        captured = {}
        class Provider(server.BaseHTTPRequestHandler):
            def do_POST(self):
                captured["path"] = self.path
                captured["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"choices": [{"message": {"content": "real contract response"}}], "usage": {"prompt_tokens": 12, "completion_tokens": 9}}).encode())
            def log_message(self, *args):
                pass
        provider = server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        try:
            agent = dict(server.BY_ID["local"], endpoint=f"http://127.0.0.1:{provider.server_port}/v1")
            result = OpenAICompatibleAdapter().complete(agent, "test")
            self.assertEqual(result.text, "real contract response")
            self.assertEqual(captured["path"], "/v1/chat/completions")
            self.assertNotIn("max_tokens", captured["body"])
            self.assertNotIn("tools", captured["body"])
            self.assertEqual(result.output_tokens, 9)
        finally:
            provider.shutdown()
            provider.server_close()

    def test_first_setup_and_password_login(self):
        client = Client()
        with patch.object(server, "MODE", "live"):
            self.assertTrue(client.request("/api/auth/status")[1]["setup_required"])
            self.assertEqual(client.request("/api/setup", {"password": "short", "bootstrap_token": server.security.bootstrap_token(server)})[0], 400)
            self.assertEqual(client.request("/api/setup", {"password": "test-password-1234", "bootstrap_token": server.security.bootstrap_token(server)})[0], 201)
            self.assertEqual(client.request("/api/setup", {"password": "another-pass-1234"})[0], 403)
            self.assertFalse(client.request("/api/auth/status")[1]["setup_required"])
            self.assertEqual(client.request("/api/login", {"user": "admin", "token": "bad"})[0], 403)
            self.assertEqual(client.request("/api/login", {"user": "admin", "token": "test-password-1234"})[0], 200)

    def test_agent_credentials_encrypted_and_never_returned(self):
        body = dict(name="My model", model="my-model", endpoint="http://127.0.0.1:11434/v1", api_key="test-private-api-key", price=.001)
        code, result = self.client.request("/api/agents", body)
        self.assertEqual(code, 201, result)
        aid = result["id"]
        self.assertEqual(server.agent_key(server.BY_ID[aid]), "test-private-api-key")
        with server.connect() as db:
            stored = db.execute("SELECT config FROM agent_configs WHERE id=?", (aid,)).fetchone()[0]
        self.assertNotIn("test-private-api-key", stored)
        for path in ("/api/agents", "/api/state"):
            code, data = self.client.request(path)
            self.assertEqual(code, 200)
            self.assertNotIn("test-private-api-key", json.dumps(data))
            self.assertNotIn("encrypted_key", json.dumps(data))
        self.assertEqual((Path(server.DB_PATH).parent / "master.key").stat().st_mode & 0o777, 0o600)
        body.update(id=aid, api_key="", name="Renamed")
        self.assertEqual(self.client.request("/api/agents", body)[0], 201)
        self.assertEqual(server.agent_key(server.BY_ID[aid]), "test-private-api-key")
        body["clear_key"] = True
        self.assertEqual(self.client.request("/api/agents", body)[0], 201)
        self.assertEqual(server.agent_key(server.BY_ID[aid]), "")

    def test_agent_settings_admin_only(self):
        self.client.login("operator")
        self.assertEqual(self.client.request("/api/agents", dict(name="a",model="b",endpoint="http://localhost/v1"))[0], 403)
        self.assertNotEqual(self.client.request("/api/agents")[0], 200)

    def test_invalid_agent_endpoint_and_active_edit(self):
        body = dict(name="Atlas", model="x", id="local", endpoint="file:///etc/passwd")
        self.assertEqual(self.client.request("/api/agents", body)[0], 400)
        body["endpoint"] = "http://localhost:11434/v1?key=bad"
        self.assertEqual(self.client.request("/api/agents", body)[0], 400)
        self.create()
        body["endpoint"] = "http://localhost:11434/v1"
        self.assertEqual(self.client.request("/api/agents", body)[0], 400)

    def test_settings_survive_restart(self):
        code, saved = self.client.request("/api/agents", dict(name="Persistent",model="test",endpoint="http://localhost:11434/v1",api_key="persisted-key"))
        self.assertEqual(code, 201)
        server.initialize()
        self.assertEqual(server.agent_key(server.BY_ID[saved["id"]]), "persisted-key")

    def test_missing_master_key_does_not_create_a_replacement(self):
        code, saved = self.client.request("/api/agents", dict(name="Vault",model="test",endpoint="http://localhost:11434/v1",api_key="protected-key"))
        self.assertEqual(code, 201)
        key_path = Path(server.DB_PATH).parent / "master.key"
        backup_path = key_path.with_suffix(".backup")
        key_path.rename(backup_path)
        try:
            with self.assertRaises(RuntimeError):
                server.agent_key(server.BY_ID[saved["id"]])
            self.assertFalse(key_path.exists())
            with self.assertRaises(RuntimeError):
                server.initialize()
        finally:
            backup_path.rename(key_path)

    def test_second_launch_does_not_recover_active_database(self):
        with patch.dict(os.environ, {"HOST": "127.0.0.1", "PORT": str(self.httpd.server_port)}), patch.object(server, "initialize") as initialize:
            with self.assertRaises(SystemExit):
                server.main()
            initialize.assert_not_called()

    def test_authenticated_pdf_and_markdown_download(self):
        tid = self.create(prompt="Türkçe doğrulama: ğĞıİşŞçÇöÖüÜ ve <script>güvenilmeyen</script>")
        data = self.process(tid)
        for fmt, content_type in (("md", "text/markdown"), ("pdf", "application/pdf")):
            response = self.client.opener.open(BASE + f"/api/tasks/{tid}/export?format={fmt}")
            output = response.read()
            self.assertIn(content_type, response.headers["Content-Type"])
            self.assertIn("attachment;", response.headers["Content-Disposition"])
            if fmt == "md":
                self.assertIn("ğĞıİşŞçÇöÖüÜ", output.decode())
                self.assertIn("Baş uzman sentezi", output.decode())
            else:
                self.assertTrue(output.startswith(b"%PDF"))
        rid = data["runs"][0]["id"]
        response = self.client.opener.open(BASE + f"/api/tasks/{tid}/export?format=md&run={rid}")
        self.assertIn("Ajan çıktısı", response.read().decode())
        self.assertEqual(Client().request(f"/api/tasks/{tid}/export?format=pdf")[0], 401)

    def test_access_password_change_revokes_sessions(self):
        self.assertEqual(self.client.request("/api/access/password", {"user":"admin","password":"changed-password-1234"})[0], 200)
        self.assertEqual(self.client.request("/api/state")[0], 401)

    def test_live_saved_agent_executes_real_http_and_connection_test(self):
        calls=[]
        class Provider(server.BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append({"auth": self.headers.get("Authorization"), "payload": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                self.send_response(200);self.send_header("Content-Type", "application/json");self.end_headers()
                self.wfile.write(json.dumps({"choices":[{"message":{"content":"Gerçek API sözleşmesi çalıştı"}}],"usage":{"prompt_tokens":15,"completion_tokens":8}}).encode())
            def log_message(self,*args):
                pass
        provider=server.ThreadingHTTPServer(("127.0.0.1",0),Provider)
        threading.Thread(target=provider.serve_forever,daemon=True).start()
        try:
            code, saved=self.client.request("/api/agents",dict(name="Own node",model="own-model",endpoint=f"http://127.0.0.1:{provider.server_port}/v1",api_key="my-node-key",price=.001))
            self.assertEqual(code,201)
            aid=saved["id"]
            with patch.object(server,"MODE","live"), patch.dict(os.environ,{"APP_MODE":"live"}):
                code, check=self.client.request(f"/api/agents/{aid}/test",{})
                self.assertEqual(code,200,check);self.assertTrue(check["ok"])
                tid=self.create(mode="single",agents=[aid])
                data=self.process(tid)
            self.assertEqual(data["task"]["status"],"completed")
            self.assertEqual(data["runs"][0]["output"],"Gerçek API sözleşmesi çalıştı")
            self.assertEqual(calls[0]["auth"],"Bearer my-node-key")
            self.assertEqual(calls[0]["payload"]["max_tokens"],32)
            self.assertEqual(len(calls),2)
        finally:
            provider.shutdown();provider.server_close()

    def test_sixteen_thousand_turkish_characters_are_accepted(self):
        prompt="ğ" * 16000
        tid=self.create(prompt=prompt,mode="single",agents=["local"])
        self.assertEqual(self.client.request('/api/tasks/'+tid)[1]['task']['prompt'],prompt)
        data=self.process(tid)
        self.assertEqual(data['task']['status'],'completed')
        code,_=self.client.request('/api/tasks',dict(prompt=prompt+'x',mode='single',agents=['local']))
        self.assertEqual(code,400)

    def test_long_unicode_input_reserves_enough_budget(self):
        self.assertGreater(server.reserve_for(['hermes'],None,'🚀'*16000),server.reserve_for(['hermes'],None,'hello'))

    def test_delete_history_removes_content_and_files_but_keeps_cost(self):
        tid=self.create(tool_requested=True)
        self.process(tid)
        aid=self.client.request('/api/state')[1]['approvals'][0]['id']
        self.client.request('/api/approvals/'+aid,{'decision':'approve'})
        artifact=Path(server.DB_PATH).parent/'artifacts'/(tid+'.txt')
        self.assertTrue(artifact.exists())
        with server.connect() as db:
            db.execute('UPDATE tasks SET cost=0.75 WHERE id=?',(tid,))
        code,data=self.client.request('/api/history/delete',{'ids':[tid]})
        self.assertEqual(code,200,data)
        self.assertEqual(data['deleted'],1)
        self.assertFalse(artifact.exists())
        self.assertEqual(self.client.request('/api/tasks/'+tid)[0],404)
        self.assertEqual(self.client.request('/api/state')[1]['usage']['cost'],.75)
        with server.connect() as db:
            for table in ('runs','logs','approvals'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table+' WHERE task_id=?',(tid,)).fetchone()[0],0)

    def test_history_clear_keeps_active_and_pending_approval_tasks(self):
        completed=self.create();self.process(completed)
        queued=self.create()
        pending=self.create(tool_requested=True);self.process(pending)
        code,data=self.client.request('/api/history/delete',{'all':True})
        self.assertEqual(code,200);self.assertEqual(data['deleted'],1)
        self.assertEqual(self.client.request('/api/tasks/'+queued)[0],200)
        self.assertEqual(self.client.request('/api/tasks/'+pending)[0],200)

    def test_history_delete_rbac_and_active_guard(self):
        tid=self.create();self.process(tid)
        self.client.login('operator')
        self.assertEqual(self.client.request('/api/history/delete',{'ids':[tid]})[0],403)
        self.assertEqual(self.client.request('/api/history/delete',{'all':True})[1]['deleted'],0)
        self.client.login('viewer')
        self.assertEqual(self.client.request('/api/history/delete',{'all':True})[0],403)
        self.client.login('admin')
        queued=self.create()
        self.assertEqual(self.client.request('/api/history/delete',{'ids':[queued]})[0],400)

    def test_agent_deactivate_and_delete_remove_secret_keep_history(self):
        code,data=self.client.request('/api/agents',dict(name='Custom agent',model='test-model',endpoint='http://localhost:11434/v1',api_key='only-test-key'))
        self.assertEqual(code,201);aid=data['id']
        tid=self.create(mode='single',agents=[aid]);self.process(tid)
        self.assertEqual(self.client.request('/api/agents/'+aid+'/toggle',{'enabled':False})[0],200)
        self.assertFalse(server.BY_ID[aid]['enabled'])
        self.assertEqual(self.client.request('/api/tasks',dict(prompt='test',mode='single',agents=[aid]))[0],400)
        self.assertEqual(self.client.request('/api/agents/'+aid+'/toggle',{'enabled':True})[0],200)
        self.assertEqual(self.client.request('/api/agents/'+aid+'/delete',{})[0],200)
        self.assertEqual(server.agent_key(server.BY_ID[aid]),'')
        self.assertFalse(any(a['id']==aid for a in self.client.request('/api/state')[1]['agents']))
        self.assertFalse(any(a['id']==aid for a in self.client.request('/api/agents')[1]['agents']))
        data=self.client.request('/api/tasks/'+tid)[1]
        self.assertEqual(data['runs'][0]['agent_name'],'Custom agent')
        server.initialize()
        self.assertTrue(server.BY_ID[aid]['deleted'])

    def test_agent_delete_and_toggle_reject_active_jobs_and_non_admin(self):
        self.create()
        self.assertEqual(self.client.request('/api/agents/hermes/delete',{})[0],400)
        self.assertEqual(self.client.request('/api/agents/hermes/toggle',{'enabled':False})[0],400)
        self.client.login('operator')
        self.assertEqual(self.client.request('/api/agents/local/delete',{})[0],403)

    def test_default_agent_can_be_disabled_without_an_endpoint(self):
        self.assertEqual(self.client.request('/api/agents/hermes/toggle',{'enabled':False})[0],200)
        self.assertFalse(server.BY_ID['hermes']['enabled'])

    def test_truncated_output_is_preserved_and_not_marked_completed(self):
        text='Uzun çıktı ' * 6000
        with patch.object(MockAdapter,'complete',return_value=Result(text,30,8192,'length')):
            tid=self.create(tool_requested=True)
            data=self.process(tid)
        self.assertEqual(data['task']['status'],'incomplete')
        self.assertEqual(len(data['runs']),2)  # No judge synthesis from incomplete evidence.
        self.assertEqual(data['runs'][0]['output'],text)
        self.assertEqual(data['runs'][0]['finish_reason'],'length')
        self.assertIsNone(data['runs'][0]['output_limit'])
        self.assertFalse(self.client.request('/api/state')[1]['approvals'])

    def test_continue_incomplete_output_creates_budgeted_single_task(self):
        with patch.object(MockAdapter,'complete',return_value=Result('Önceki yanıtın sonu',10,8192,'length')):
            tid=self.create(mode='single',agents=['local'])
            data=self.process(tid)
        rid=data['runs'][0]['id']
        code,result=self.client.request('/api/tasks/'+tid+'/continue',{'run_id':rid})
        self.assertEqual(code,201,result)
        task=self.client.request('/api/tasks/'+result['id'])[1]['task']
        self.assertEqual(task['mode'],'single')
        with server.connect() as db:
            self.assertIn('Önceki yanıtın sonu',db.execute('SELECT continuation_context FROM tasks WHERE id=?',(result['id'],)).fetchone()[0])
        self.assertLessEqual(len(task['prompt']),16000)
        self.client.login('viewer')
        self.assertEqual(self.client.request('/api/tasks/'+tid+'/continue',{'run_id':rid})[0],403)

    def test_legacy_output_limit_settings_are_not_used(self):
        code,data=self.client.request('/api/agents',dict(name='Unlimited answer',model='test',endpoint='http://localhost:11434/v1',max_output_tokens=1500,price=.01))
        self.assertEqual(code,201,data)
        self.assertNotIn('max_output_tokens',server.BY_ID[data['id']])

    def test_legacy_token_cap_is_marked_as_suspected_not_silently_completed(self):
        tid=self.create(mode='single',agents=['local']);data=self.process(tid)
        with server.connect() as db:
            db.execute("UPDATE runs SET output_tokens=1500,finish_reason=NULL,output_limit=NULL WHERE task_id=?",(tid,))
        server.initialize()
        data=self.client.request('/api/tasks/'+tid)[1]
        self.assertEqual(data['task']['status'],'incomplete')
        self.assertEqual(data['runs'][0]['finish_reason'],'legacy_length_suspected')

    def test_provider_response_over_two_megabytes_is_not_cut(self):
        content='log entry\n'*240000
        captured={}
        class Provider(server.BaseHTTPRequestHandler):
            def do_POST(self):
                captured.update(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.end_headers()
                self.wfile.write(json.dumps({'choices':[{'message':{'content':content},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':500000}}).encode())
            def log_message(self,*args):pass
        provider=server.ThreadingHTTPServer(('127.0.0.1',0),Provider)
        threading.Thread(target=provider.serve_forever,daemon=True).start()
        try:
            agent=dict(server.BY_ID['local'],endpoint=f'http://127.0.0.1:{provider.server_port}/v1',max_output_tokens=1500)
            result=OpenAICompatibleAdapter().complete(agent,'Read the logs')
            self.assertEqual(result.text,content)
            self.assertNotIn('max_tokens',captured)
            self.assertNotIn('max_output_tokens',captured)
        finally:
            provider.shutdown();provider.server_close()

    def test_judge_receives_full_participant_output(self):
        content='İncelenecek kayıtlar\n'*10000
        contexts=[]
        def complete(agent,prompt,context=''):
            if context:
                contexts.append(context)
                return Result('Tam kayıtları değerlendirdim',10,10)
            return Result(content,10,100000)
        with patch.object(MockAdapter,'complete',side_effect=complete):
            data=self.process(self.create())
        self.assertEqual(data['task']['status'],'completed')
        self.assertIn(content,contexts[0])
        self.assertEqual(data['runs'][0]['output'],content)

    def test_provider_summary_deduplicates_endpoints_and_uses_results(self):
        for aid in ('local','research'):
            code,_=self.client.request('/api/agents',dict(id=aid,name=aid,model='test',endpoint='http://localhost:11434/v1'))
            self.assertEqual(code,201)
        self.assertEqual(self.client.request('/api/state')[1]['providers'],dict(working=0,not_working=0,unknown=1,total=1))
        self.process(self.create(mode='single',agents=['local']))
        self.assertEqual(self.client.request('/api/state')[1]['providers']['working'],1)
        for aid in ('local','research'):
            self.client.request('/api/agents/'+aid+'/toggle',{'enabled':False})
        self.assertEqual(self.client.request('/api/state')[1]['providers']['not_working'],1)

    def test_web_requires_onboarded_mcp_and_is_off_by_default(self):
        tid=self.create(mode='single',agents=['local'])
        self.assertEqual(self.client.request('/api/tasks/'+tid)[1]['task']['web_enabled'],0)
        with patch.object(server,'browser_status',return_value={'installed':False}):
            self.assertEqual(self.client.request('/api/tasks',dict(prompt='search',mode='single',agents=['local'],web_enabled=True))[0],400)
        self.client.login('viewer')
        self.assertEqual(self.client.request('/api/browser/install',{})[0],403)

    def test_public_web_guard_blocks_private_addresses(self):
        from app.browser_mcp import validate_url
        for url in ('file:///etc/passwd','http://127.0.0.1','http://169.254.169.254','https://localhost:8080'):
            with self.assertRaises(ValueError):validate_url(url)

    def test_web_tool_roundtrip_and_usage_accounting(self):
        messages=[]
        class Provider(server.BaseHTTPRequestHandler):
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));messages.append(body)
                if len(messages)==1:
                    result={'content':None,'tool_calls':[{'id':'call-test','type':'function','function':{'name':'web_search','arguments':json.dumps({'query':'public security news'})}}]}
                    finish='tool_calls'
                else:
                    result={'content':'Source: https://example.com'};finish='stop'
                self.send_response(200);self.end_headers()
                self.wfile.write(json.dumps({'choices':[{'message':result,'finish_reason':finish}],'usage':{'prompt_tokens':10,'completion_tokens':20}}).encode())
            def log_message(self,*args):pass
        class Web:
            def __init__(self):self.calls=[]
            def call(self,name,arguments):self.calls.append((name,arguments));return 'Public source: https://example.com'
        web=Web();provider=server.ThreadingHTTPServer(('127.0.0.1',0),Provider)
        threading.Thread(target=provider.serve_forever,daemon=True).start()
        try:
            agent=dict(server.BY_ID['local'],endpoint=f'http://127.0.0.1:{provider.server_port}/v1',_web=web)
            result=OpenAICompatibleAdapter().complete(agent,'Research public security news')
            self.assertEqual(result.finish_reason,'stop')
            self.assertEqual(result.input_tokens,20);self.assertEqual(result.output_tokens,40)
            self.assertEqual(web.calls[0][0],'web_search')
            self.assertEqual(len(messages),2)
            self.assertEqual(messages[1]['messages'][-1]['role'],'tool')
            self.assertNotIn('max_tokens',messages[1])
            self.assertEqual({tool['function']['name'] for tool in messages[0]['tools']},{'web_search','web_open'})
        finally:provider.shutdown();provider.server_close()

    def test_reasoning_is_preserved_and_empty_final_is_repaired_without_more_web(self):
        calls=[{'id':'search-one','type':'function','function':{'name':'web_search','arguments':'{"query":"public reference"}'}}]
        responses=[model_response(None,'tool_calls',tool_calls=calls,reasoning_content='opaque-provider-state-1'),
                   model_response(None,reasoning_content='opaque-provider-state-2'),model_response('Final report with https://example.com')]
        class Web:
            def __init__(self):self.count=0
            def call(self,*args):self.count+=1;return 'Verified public source: https://example.com'
        web=Web()
        with scripted_provider(responses) as (endpoint,requests),patch.dict(os.environ,{'WEB_MAX_TOOL_STEPS':'1'}):
            result=OpenAICompatibleAdapter().complete(dict(server.BY_ID['local'],endpoint=endpoint,_web=web),'Research')
        self.assertEqual(result.finish_reason,'stop');self.assertTrue(result.text.strip())
        self.assertEqual(web.count,1);self.assertEqual(len(requests),3)
        self.assertEqual(result.input_tokens,30);self.assertEqual(result.output_tokens,15)
        self.assertEqual(requests[1]['tool_choice'],'none')
        self.assertTrue(any(m.get('reasoning_content')=='opaque-provider-state-1' for m in requests[1]['messages']))
        self.assertTrue(any(m.get('reasoning_content')=='opaque-provider-state-2' for m in requests[2]['messages']))
        self.assertNotIn('max_tokens',requests[2])

    def test_repeated_empty_final_stays_incomplete_and_accounts_all_usage(self):
        with scripted_provider([model_response(None),model_response('   ')]) as (endpoint,requests):
            result=OpenAICompatibleAdapter().complete(dict(server.BY_ID['local'],endpoint=endpoint),'Answer')
        self.assertEqual(result.finish_reason,'empty_response')
        self.assertEqual(len(requests),2)
        self.assertEqual(result.input_tokens,20);self.assertEqual(result.output_tokens,10)

    def test_blank_mock_output_cannot_complete_task_or_trigger_tool_or_judge(self):
        with patch.object(MockAdapter,'complete',return_value=Result('',12,15,'stop')):
            data=self.process(self.create(tool_requested=True))
        self.assertEqual(data['task']['status'],'incomplete')
        self.assertEqual(len(data['runs']),2)
        self.assertTrue(all(r['finish_reason']=='empty_response' for r in data['runs']))
        self.assertFalse(self.client.request('/api/state')[1]['approvals'])

    def test_legacy_empty_completion_is_reclassified_without_changing_cost(self):
        tid=self.create(mode='single',agents=['local']);self.process(tid)
        with server.connect() as db:
            db.execute("UPDATE runs SET output='',output_tokens=1377,finish_reason='stop' WHERE task_id=?",(tid,))
            db.execute('UPDATE tasks SET cost=.6246 WHERE id=?',(tid,))
        server.initialize()
        data=self.client.request('/api/tasks/'+tid)[1]
        self.assertEqual(data['task']['status'],'incomplete')
        self.assertAlmostEqual(data['task']['cost'],.6246)
        self.assertEqual(data['runs'][0]['finish_reason'],'empty_response')

    def test_structured_text_blocks_are_read_as_report_text(self):
        with scripted_provider([model_response([{'type':'text','text':'# Report'},{'type':'output_text','text':'Actual answer'}])]) as (endpoint,requests):
            result=OpenAICompatibleAdapter().complete(dict(server.BY_ID['local'],endpoint=endpoint),'Answer')
        self.assertEqual(result.text,'# Report\nActual answer')

    def test_cached_evidence_retry_does_not_repeat_browser_work_or_expose_reasoning(self):
        calls=[{'id':'cached-source','type':'function','function':{'name':'web_open','arguments':'{"url":"https://example.com"}'}}]
        responses=[model_response(None,'tool_calls',tool_calls=calls,reasoning_content='opaque-hidden-state'),
                   model_response(None),model_response(None),model_response('Recovered report from cached source')]
        class Web:
            def __init__(self):self.count=0
            def call(self,*args):self.count+=1;return 'Cached verified source text'
            def close(self):pass
        web=Web()
        with scripted_provider(responses) as (endpoint,requests):
            code,saved=self.client.request('/api/agents',dict(name='Cache test',model='test',endpoint=endpoint,price=.001))
            self.assertEqual(code,201)
            with patch.object(server,'MODE','live'),patch.dict(os.environ,{'APP_MODE':'live','WEB_MAX_TOOL_STEPS':'1'}),patch.object(server,'browser_status',return_value={'installed':True}),patch.object(server,'BrowserMCP',return_value=web) as browser:
                tid=self.create(mode='single',agents=[saved['id']],web_enabled=True)
                data=self.process(tid)
                self.assertEqual(data['task']['status'],'incomplete')
                run=data['runs'][0]
                self.assertTrue(run['can_resume_from_evidence'])
                self.assertNotIn('provider_state',run)
                self.assertNotIn('opaque-hidden-state',json.dumps(data))
                code,retry=self.client.request('/api/tasks/'+tid+'/continue',{'run_id':run['id']})
                self.assertEqual(code,201,retry)
                recovered=self.process(retry['id'])
                self.assertEqual(recovered['task']['status'],'completed')
                self.assertEqual(recovered['task']['web_enabled'],0)
                self.assertIn('Recovered report',recovered['runs'][0]['output'])
                self.assertEqual(web.count,1);self.assertEqual(browser.call_count,1)
                self.assertEqual(requests[-1]['tool_choice'],'none')


if __name__ == "__main__":
    unittest.main()
