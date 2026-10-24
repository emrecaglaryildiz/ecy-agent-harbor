"""Workspace regression tests use isolated storage and fake providers/Telegram only."""
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError

os.environ['APP_MODE'] = 'mock'
os.environ['MOCK_DELAY'] = '0'
from app import server, files, workspace, pipeline, automation
from app.adapters import Result, OpenAICompatibleAdapter
from app.streaming import read_completion
import test_harbor as helpers


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        server.DB_PATH = str(Path(cls.temp.name) / 'workspace.db')
        server.initialize()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        helpers.BASE = f'http://127.0.0.1:{cls.httpd.server_port}'
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.temp.cleanup()

    def setUp(self):
        with server.connect() as db:
            for table in ('logs','approvals','runs','tasks','files','projects','templates','schedules','integrations','deleted_costs'):
                db.execute('DELETE FROM ' + table)
            db.execute('DELETE FROM agent_configs')
            for a in server.DEFAULT_AGENTS:
                db.execute('INSERT INTO agent_configs VALUES(?,?,?)', (a['id'],json.dumps(a),server.now()))
        server.reload_agents()
        self.client = helpers.Client(); self.client.login('admin')
        self.user = {'id':'admin','name':'Emre','role':'admin'}

    def project(self):
        code, data = self.client.request('/api/projects', {'name':'Olay analizi','instruction':'Türkçe raporla'})
        self.assertEqual(code, 201)
        return data['id']

    def upload(self, content='ilk satır\nson satır\n', project=None, name='olay.log'):
        code, data = self.client.request('/api/files', {'name':name,'data':base64.b64encode(content.encode()).decode(),'project_id':project})
        self.assertEqual(code, 201, data)
        return data['id']

    def config(self, **kw):
        return dict(prompt='Kanıtları incele',mode='single',agents=['local'],**kw)

    def task(self, **kw):
        tid = server.create_task(self.user, self.config(**kw))
        with server.connect() as db:
            db.execute("UPDATE tasks SET status='running' WHERE id=?", (tid,))
        return tid

    def test_upload_project_scope_and_evidence_view(self):
        pid=self.project(); fid=self.upload(project=pid)
        code, data=self.client.request('/api/files/'+fid+'?start=2&count=1')
        self.assertEqual(data['lines'],['son satır'])
        self.assertNotIn('text',data['file'])
        code, data=self.client.request('/api/tasks', self.config(file_ids=[fid]))
        self.assertEqual(code,400)
        tid=self.task(project_id=pid,file_ids=[fid])
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT file_id FROM task_files WHERE task_id=?',(tid,)).fetchone()[0],fid)
        _, scoped=self.client.request('/api/state?project_id='+pid)
        _, general=self.client.request('/api/state?project_id=__general')
        self.assertEqual(len(scoped['tasks']),1); self.assertEqual(general['tasks'],[])

    def test_chunks_cover_all_characters_and_giant_lines(self):
        text='İ'*40001+'\nnormal\n'+'X'*17000
        parts=list(files.chunks({'text':text},16000))
        self.assertEqual(''.join(p[2] for p in parts),text)
        self.assertTrue(all(len(p[2])<=16000 for p in parts))
        self.assertEqual(parts[0][:2],(1,1));self.assertEqual(parts[-1][1],3)

    def test_upload_rejects_paths_binary_and_viewer(self):
        for name,raw in [('../secret.log',b'hello'),('a.exe',b'data'),('a.log',b'\0data'),('a.png',b'not png')]:
            code,_=self.client.request('/api/files',{'name':name,'data':base64.b64encode(raw).decode()})
            self.assertEqual(code,400)
        self.client.login('viewer')
        code,_=self.client.request('/api/files',{'name':'a.log','data':'eA=='})
        self.assertEqual(code,403)

    def test_pdf_extraction(self):
        from app.exports import pdf_bytes
        raw=pdf_bytes('# Kanıt\nBu bir olay kaydıdır.')
        code,data=self.client.request('/api/files',{'name':'rapor.pdf','data':base64.b64encode(raw).decode()})
        self.assertEqual(code,201,data)
        _, view=self.client.request('/api/files/'+data['id'])
        self.assertIn('olay',' '.join(view['lines']))

    def test_all_log_chunks_processed_and_sources_exported(self):
        fid=self.upload('A'*16001+'\nTAIL_SENTINEL\n')
        tid=self.task(file_ids=[fid]);captured=[]
        class Model:
            def complete(inner,agent,prompt,context=''):
                captured.append((prompt,context))
                return Result(f'Bulgu [dosya:{fid}:L1-L2]',10,5)
        with patch.object(server,'adapter_for',return_value=Model()):server.process_task(tid)
        self.assertTrue(any('TAIL_SENTINEL' in p for p,c in captured))
        self.assertEqual(len(captured),3)
        _,detail=self.client.request('/api/tasks/'+tid)
        self.assertEqual(detail['task']['status'],'completed');self.assertTrue(detail['sources'][0]['valid_range'])
        from app.exports import markdown_report
        self.assertIn('Kaynak envanteri',markdown_report(dict(detail['task'],sources=detail['sources']),detail['runs'],server.BY_ID))

    def test_council_has_independent_reviews_and_judge(self):
        tid=server.create_task(self.user,dict(prompt='İncele',mode='council',agents=['local','research'],judge='critic',review_enabled=True))
        with server.connect() as db:db.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,))
        with patch.object(server,'adapter_for',return_value=type('Model',(),{'complete':lambda *a:Result('Kanıt ve belirsizlik',10,5)})()):server.process_task(tid)
        _,detail=self.client.request('/api/tasks/'+tid)
        self.assertEqual(detail['task']['status'],'completed')
        kinds=[r['kind'] for r in detail['runs']]
        self.assertEqual(kinds.count('review'),2);self.assertEqual(kinds.count('judge'),1)

    def test_fallback_is_explicit_and_keeps_failure_history(self):
        tid=self.task(fallback_agent='research')
        class Model:
            def complete(inner,agent,*args):
                if agent['id']=='local':raise ValueError('Do not disclose provider secret')
                return Result('Yedek çıktı',10,5)
        with patch.object(server,'adapter_for',return_value=Model()):server.process_task(tid)
        _,detail=self.client.request('/api/tasks/'+tid)
        self.assertEqual(detail['task']['status'],'completed')
        self.assertEqual([r['status'] for r in detail['runs']],['failed','completed'])
        self.assertNotIn('secret',json.dumps(detail))

    def test_stream_decoder_private_reasoning_fragmented_tools_and_usage(self):
        stream=[{'choices':[{'index':0,'delta':{'reasoning_content':'private','content':'Mer'}}]},
                {'choices':[{'index':0,'delta':{'content':'haba','tool_calls':[{'index':0,'id':'call1','function':{'name':'web_open','arguments':'{"url":'}}]}}]},
                {'choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"https://example.com"}'}}]},'finish_reason':'tool_calls'}]},
                {'choices':[],'usage':{'prompt_tokens':5,'completion_tokens':2}}]
        class Response(io.BytesIO):headers={'Content-Type':'text/event-stream'}
        deltas=[];r=read_completion(Response((''.join('data: '+json.dumps(i)+'\n\n' for i in stream)+'data: [DONE]\n\n').encode()),{'_on_delta':deltas.append})
        self.assertEqual(''.join(deltas),'Merhaba');self.assertEqual(r['choices'][0]['message']['reasoning_content'],'private')
        self.assertEqual(json.loads(r['choices'][0]['message']['tool_calls'][0]['function']['arguments'])['url'],'https://example.com')
        self.assertEqual(r['usage']['prompt_tokens'],5)

    def test_stream_without_finish_remains_incomplete(self):
        class Response(io.BytesIO):headers={'Content-Type':'text/event-stream'}
        r=read_completion(Response(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'),{})
        self.assertEqual(r['choices'][0]['finish_reason'],'stream_interrupted')

    def test_live_delta_is_persisted_before_final_response(self):
        tid=self.task();arrived=threading.Event();release=threading.Event()
        class Model:
            def complete(inner,agent,*args):
                agent['_on_delta']('İlk canlı parça');arrived.set();release.wait(3)
                return Result('Nihai çıktı',10,5)
        with patch.object(server,'adapter_for',return_value=Model()):
            t=threading.Thread(target=server.run_agent,args=(tid,'local','İncele'));t.start()
            try:
                self.assertTrue(arrived.wait(2))
                with server.connect() as db:self.assertEqual(db.execute('SELECT output FROM runs WHERE task_id=?',(tid,)).fetchone()[0],'İlk canlı parça')
            finally:release.set();t.join(3)

    def test_transient_retry_before_output_only(self):
        tid=self.task();count=[]
        class Model:
            def complete(inner,agent,*args):
                count.append(1)
                if len(count)==1:raise URLError('temporary')
                return Result('Tamam',10,5)
        with patch.object(server,'adapter_for',return_value=Model()),patch.object(server.STOP,'wait',return_value=False):server.run_agent(tid,'local','İncele')
        self.assertEqual(len(count),2)
        with server.connect() as db:self.assertEqual(db.execute('SELECT attempt FROM runs').fetchone()[0],2)

    def test_partial_output_does_not_auto_retry(self):
        tid=self.task();count=[]
        class Model:
            def complete(inner,agent,*args):
                count.append(1);agent['_on_delta']('Korunacak parça');raise URLError('interrupted')
        with patch.object(server,'adapter_for',return_value=Model()):server.run_agent(tid,'local','İncele')
        self.assertEqual(len(count),1)
        with server.connect() as db:self.assertEqual(db.execute('SELECT output FROM runs').fetchone()[0],'Korunacak parça')

    def test_template_and_schedule_role_boundaries(self):
        _,data=self.client.request('/api/templates',{'name':'İnceleme','config':self.config()})
        self.assertIn('id',data)
        self.client.login('viewer')
        for path,body in [('/api/projects',{'name':'x'}),('/api/schedules',{'name':'x','config':self.config(),'interval_minutes':5}),('/api/telegram',{})]:
            self.assertEqual(self.client.request(path,body)[0],403)

    def test_schedule_dispatch_atomic_once_and_no_overdue_burst(self):
        code,data=self.client.request('/api/schedules',{'name':'Günlük inceleme','config':self.config(),'interval_minutes':5,'next_run':time.time()-1})
        self.assertEqual(code,201,data)
        stamp=time.time()
        threads=[threading.Thread(target=automation.schedule_tick,args=(server,stamp)) for _ in range(2)]
        [t.start() for t in threads];[t.join() for t in threads]
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
            row=db.execute('SELECT * FROM schedules').fetchone();self.assertGreater(row['next_run'],stamp)
        automation.schedule_tick(server,stamp+301)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)

    def test_invalid_schedule_pauses_with_explanation(self):
        self.client.request('/api/schedules',{'name':'İnceleme','config':self.config(),'interval_minutes':5,'next_run':time.time()-1})
        with server.connect() as db:
            cfg=dict(server.BY_ID['local'],enabled=False)
            db.execute('UPDATE agent_configs SET config=? WHERE id=?',(json.dumps(cfg),'local'))
        server.reload_agents();automation.schedule_tick(server)
        with server.connect() as db:
            row=db.execute('SELECT * FROM schedules').fetchone();self.assertFalse(row['enabled']);self.assertTrue(row['last_error'])

    def test_telegram_encrypted_opt_in_and_minimal_message(self):
        code,_=self.client.request('/api/telegram',{'enabled':True,'token':'123:test-token','chat_id':'123'})
        self.assertEqual(code,201)
        _,meta=self.client.request('/api/workspace')
        self.assertTrue(meta['telegram']['has_token']);self.assertNotIn('test-token',json.dumps(meta))
        with server.connect() as db:self.assertNotIn('test-token',db.execute('SELECT config FROM integrations').fetchone()[0])
        tid=self.task()
        with server.connect() as db:db.execute("UPDATE tasks SET status='completed',synthesis='PRIVATE REPORT',updated_at=? WHERE id=?",(server.now(),tid))
        automation.enqueue_notifications(server);automation.enqueue_notifications(server)
        captured=[]
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        class Fake:
            def open(self,request,timeout):captured.append(json.loads(request.data));return Response(b'{"ok":true}')
        automation.send_one(server,Fake());automation.send_one(server,Fake())
        self.assertEqual(len(captured),1);self.assertNotIn('PRIVATE REPORT',captured[0]['text']);self.assertNotIn('Kanıtları incele',captured[0]['text'])
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM notifications').fetchone()[0],'sent')

    def test_uncertain_telegram_delivery_not_replayed(self):
        self.client.request('/api/telegram',{'enabled':True,'token':'123:test','chat_id':'123'})
        tid=self.task()
        with server.connect() as db:db.execute("UPDATE tasks SET status='completed',updated_at=? WHERE id=?",(server.now(),tid))
        automation.enqueue_notifications(server)
        class Fake:
            def open(self,*args,**kwargs):raise TimeoutError()
        automation.send_one(server,Fake())
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM notifications').fetchone()[0],'unknown')
        with patch.object(Fake,'open') as send:automation.send_one(server,Fake());send.assert_not_called()

    def test_used_evidence_cannot_be_deleted_and_followup_retains_files(self):
        fid=self.upload();tid=self.task(file_ids=[fid])
        with server.connect() as db:db.execute("UPDATE tasks SET status='completed' WHERE id=?",(tid,))
        self.assertEqual(self.client.request('/api/files/delete',{'id':fid})[0],400)
        code,data=self.client.request('/api/tasks/'+tid+'/followup',{'prompt':'Bunu ayrıntılandır'})
        self.assertEqual(code,201,data)
        with server.connect() as db:self.assertEqual(db.execute('SELECT file_id FROM task_files WHERE task_id=?',(data['id'],)).fetchone()[0],fid)

    def test_retry_uses_saved_stage_without_private_state_in_api(self):
        tid=self.task()
        class Model:
            def complete(self,*args):raise ValueError()
        with patch.object(server,'adapter_for',return_value=Model()):server.process_task(tid)
        _,data=self.client.request('/api/tasks/'+tid);run=data['runs'][0]
        self.assertNotIn('request_state',run)
        code,next_task=self.client.request('/api/tasks/'+tid+'/retry',{'run_id':run['id']})
        self.assertEqual(code,201,next_task)
        with server.connect() as db:
            request=json.loads(db.execute('SELECT retry_state FROM tasks WHERE id=?',(next_task['id'],)).fetchone()[0]);self.assertEqual(request['prompt'],'Kanıtları incele')

    def test_http_streaming_visible_before_completion_and_reasoning_private(self):
        first=threading.Event();release=threading.Event();captured=[]
        class Provider(server.BaseHTTPRequestHandler):
            def do_POST(inner):
                captured.append(json.loads(inner.rfile.read(int(inner.headers['Content-Length']))))
                inner.send_response(200);inner.send_header('Content-Type','text/event-stream');inner.end_headers()
                inner.wfile.write(b'data: {"choices":[{"index":0,"delta":{"reasoning_content":"PRIVATE_REASON","content":"Visible fragment"}}]}\n\n');inner.wfile.flush();first.set();release.wait(3)
                inner.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":2}}\n\ndata: [DONE]\n\n');inner.wfile.flush()
            def log_message(self,*args):pass
        provider=server.ThreadingHTTPServer(('127.0.0.1',0),Provider)
        threading.Thread(target=provider.serve_forever,daemon=True).start()
        tid=self.task()
        try:
            with patch.dict(server.BY_ID['local'],endpoint=f'http://127.0.0.1:{provider.server_port}/v1'),patch.object(server,'adapter_for',return_value=OpenAICompatibleAdapter()):
                job=threading.Thread(target=server.run_agent,args=(tid,'local','Analyze'));job.start()
                try:
                    self.assertTrue(first.wait(2))
                    deadline=time.time()+2;visible=''
                    while time.time()<deadline:
                        with server.connect() as db:visible=db.execute('SELECT output FROM runs WHERE task_id=?',(tid,)).fetchone()[0]
                        if visible:break
                        time.sleep(.01)
                    self.assertEqual(visible,'Visible fragment');self.assertNotIn('PRIVATE_REASON',visible)
                finally:release.set();job.join(4)
            self.assertTrue(captured[0]['stream'])
            with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM runs WHERE task_id=?',(tid,)).fetchone()[0],'completed')
        finally:provider.shutdown();provider.server_close()

    def test_fallback_agent_cannot_change_during_active_task(self):
        self.task(fallback_agent='research')
        with self.assertRaises(ValueError):server.change_agent_state(self.user,'research',enabled=False)
