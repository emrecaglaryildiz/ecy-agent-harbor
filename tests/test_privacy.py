"""Privacy previews never call models; fake adapters prove outgoing text boundaries."""
import os
os.environ['APP_MODE']='mock'
import json
import unittest
from unittest.mock import patch
from app import server,privacy
from app.adapters import OpenAICompatibleAdapter,HermesBridgeAdapter
import test_workspace as wh

class PrivacyTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    config=wh.WorkspaceTests.config
    upload=wh.WorkspaceTests.upload
    project=wh.WorkspaceTests.project
    def setUp(self):
        wh.WorkspaceTests.setUp(self)
        with server.connect() as db:db.execute('DELETE FROM context_previews');db.execute('DELETE FROM knowledge')
    def test_redaction_is_stable_idempotent_and_preserves_non_sensitive_evidence(self):
        text='api_key=supersecret email alice@example.com 10.2.3.4 uid=123 sk-proj-abcdefghijklmnopqrstuvwxyz'
        masked=privacy.redact(text,'seed');self.assertNotIn('supersecret',masked);self.assertNotIn('alice@example.com',masked);self.assertNotIn('sk-proj-',masked)
        self.assertIn('10.2.3.4 uid=123',masked);self.assertEqual(privacy.redact(masked,'seed'),masked);self.assertNotEqual(masked,privacy.redact(text,'other'))
    def test_nested_tool_arguments_are_scrubbed(self):
        obj={'password':'abcdef','nested':[{'q':'alice@example.com'}],'count':5}
        result=privacy.scrub(obj,'seed');self.assertNotIn('abcdef',json.dumps(result));self.assertNotIn('alice@example.com',json.dumps(result));self.assertEqual(result['count'],5);self.assertEqual(obj['password'],'abcdef')
    def test_preview_sources_are_explicit_and_do_not_dispatch(self):
        fid=self.upload('email alice@example.com');cfg=self.config(mask_sensitive=True,file_ids=[fid])
        code,p=self.client.request('/api/context/preview',cfg);self.assertEqual(code,200,p);self.assertNotIn('seed',p)
        code,page=self.client.request('/api/context/page',{'id':p['id'],'config':cfg,'source_id':'file:'+fid});self.assertEqual(code,200,page);self.assertNotIn('alice@example.com',page['text']);self.assertTrue(page['changed'])
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],0);self.assertIn('alice@example.com',db.execute('SELECT text FROM files WHERE id=?',(fid,)).fetchone()[0])
    def test_preview_is_one_use_and_rejects_changed_input(self):
        cfg=self.config(mask_sensitive=True);_,p=self.client.request('/api/context/preview',cfg)
        self.assertEqual(self.client.request('/api/tasks',dict(cfg,prompt='changed',context_preview_id=p['id']))[0],400)
        code,r=self.client.request('/api/tasks',dict(cfg,context_preview_id=p['id']));self.assertEqual(code,201,r)
        self.assertEqual(self.client.request('/api/tasks',dict(cfg,context_preview_id=p['id']))[0],400)
        with server.connect() as db:
            task=server.task_dict(db.execute('SELECT * FROM tasks WHERE id=?',(r['id'],)).fetchone());self.assertTrue(task['mask_sensitive']);self.assertNotIn('privacy_seed',task)
    def test_source_mutation_invalidates_preview(self):
        pid=self.project();cfg=self.config(project_id=pid,mask_sensitive=True);_,p=self.client.request('/api/context/preview',cfg)
        with server.connect() as db:db.execute('UPDATE projects SET instruction=? WHERE id=?',('Changed',pid))
        self.assertEqual(self.client.request('/api/tasks',dict(cfg,context_preview_id=p['id']))[0],400)
    def test_preview_owner_and_viewer_boundaries(self):
        cfg=self.config(mask_sensitive=True);_,p=self.client.request('/api/context/preview',cfg)
        self.client.login('operator');self.assertEqual(self.client.request('/api/context/page',{'id':p['id'],'config':cfg,'source_id':'prompt'})[0],400)
        self.client.login('viewer');self.assertEqual(self.client.request('/api/context/preview',cfg)[0],403)
    def test_openai_rounds_and_actual_mcp_arguments_are_masked(self):
        sent=[];tool=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
        class Opener:
            def open(self,request,**kw):sent.append(json.loads(request.data));return Response()
        class Web:
            def call(self,name,args):tool.append(args);return 'password=toolsecret contact alice@example.com'
        replies=[{'choices':[{'message':{'content':'','tool_calls':[{'id':'call1','type':'function','function':{'name':'web_search','arguments':json.dumps({'query':'alice@example.com','password':'toolpass'})}}]},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':1,'completion_tokens':1}}, {'choices':[{'message':{'content':'Done'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}}]
        agent=dict(server.BY_ID['local'],endpoint='https://provider.invalid/v1',api_key='auth-intentionally-unmasked',_privacy_seed='seed',_web=Web())
        with patch('urllib.request.build_opener',return_value=Opener()),patch('app.adapters.read_completion',side_effect=replies):result=OpenAICompatibleAdapter().complete(agent,'password=promptsecret alice@example.com')
        wire=json.dumps(sent);self.assertNotIn('promptsecret',wire);self.assertNotIn('toolsecret',wire);self.assertNotIn('alice@example.com',wire);self.assertNotIn('toolpass',json.dumps(tool));self.assertEqual(result.text,'Done')
    def test_hermes_payload_is_masked(self):
        sent=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self):return json.dumps({'output':'Done','finish_reason':'stop'}).encode()
        class Opener:
            def open(self,request,**kw):sent.append(json.loads(request.data));return Response()
        agent=dict(server.BY_ID['hermes'],safe_mode=True,api_key='fake-auth',endpoint='https://bridge.invalid',_privacy_seed='seed')
        with patch('urllib.request.build_opener',return_value=Opener()):HermesBridgeAdapter().complete(agent,'password=secretvalue','alice@example.com')
        self.assertNotIn('secretvalue',json.dumps(sent));self.assertNotIn('alice@example.com',json.dumps(sent))
    def test_retry_preserves_masking_preference(self):
        tid=server.create_task(self.user,self.config(mask_sensitive=True))
        with server.connect() as db:
            db.execute("UPDATE tasks SET status='failed' WHERE id=?",(tid,));db.execute("INSERT INTO runs(id,task_id,agent_id,kind,status,output,created_at,request_state) VALUES(?,?,'local','participant','failed','',?,?)",(tid+'r',tid,server.now(),json.dumps({'prompt':'password=retrysecret','context':''})))
        code,r=self.client.request('/api/tasks/'+tid+'/retry',{'run_id':tid+'r'});self.assertEqual(code,201,r)
        with server.connect() as db:self.assertEqual(db.execute('SELECT mask_sensitive FROM tasks WHERE id=?',(r['id'],)).fetchone()[0],1)

    def test_pipeline_masks_each_chunk_project_and_final_request(self):
        pid=self.project();fid=self.upload('password=filesecret alice@example.com',project=pid)
        with server.connect() as db:db.execute('UPDATE projects SET instruction=? WHERE id=?',('password=projectsecret',pid))
        tid=server.create_task(self.user,self.config(mask_sensitive=True,file_ids=[fid],project_id=pid));sent=[]
        class Adapter:
            def complete(inner,agent,prompt,context=''):
                sent.append((prompt,context,agent['instruction']))
                from app.adapters import Result
                return Result('Evidence summary',1,1)
        with server.connect() as db:db.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,))
        with patch.object(server,'adapter_for',return_value=Adapter()):server.process_task(tid)
        self.assertGreaterEqual(len(sent),2);self.assertNotIn('filesecret',json.dumps(sent));self.assertNotIn('alice@example.com',json.dumps(sent));self.assertNotIn('projectsecret',json.dumps(sent))

    def test_image_consent_requires_boolean_true(self):
        import base64
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==')
        code,f=self.client.request('/api/files',{'name':'sample.png','data':base64.b64encode(png).decode()});self.assertEqual(code,201,f)
        cfg=self.config(mask_sensitive=True,file_ids=[f['id']])
        self.assertEqual(self.client.request('/api/tasks',dict(cfg,allow_unmasked_images='yes'))[0],400)
        self.assertEqual(self.client.request('/api/context/preview',cfg)[0],400)
        self.assertEqual(self.client.request('/api/context/preview',dict(cfg,allow_unmasked_images=True))[0],200)

    def test_large_nonmatching_tokens_and_unclosed_pem_are_safe(self):
        text='a'*200000+' '+('eyJaaaaaaaaa-'*20000)
        self.assertEqual(privacy.redact(text,'seed'),text)
        masked=privacy.redact('prefix -----BEGIN RSA PRIVATE KEY----- hidden remainder','seed')
        self.assertNotIn('hidden remainder',masked);self.assertTrue(masked.startswith('prefix [MASKED_PRIVATE_KEY_'))

    def test_quoted_secret_with_spaces_is_fully_masked(self):
        source='{"password":"two word secret", "email":"alice@example.com"}'
        clean=privacy.redact(source,'seed');self.assertNotIn('two word secret',clean);self.assertNotIn('word secret',clean);self.assertEqual(privacy.redact(clean,'seed'),clean)

    def test_signed_reasoning_and_protocol_ids_survive_text_redaction(self):
        opaque=[{'type':'reasoning.encrypted','data':'eyJaaaaaaaaaa.bbbbbbbbbbb.cccccccccccc','signature':'provider-signature'}]
        body={'model':'model-id','messages':[{'role':'assistant','content':'password=secret','reasoning_details':opaque,'tool_calls':[{'id':'call-123','type':'function','function':{'name':'lookup','arguments':json.dumps({'password':'two word secret'})}}]}]}
        wire=privacy.outbound(body,'seed');message=wire['messages'][0]
        self.assertEqual(message['reasoning_details'],opaque);self.assertEqual(message['tool_calls'][0]['id'],'call-123');self.assertNotIn('two word secret',json.dumps(message['tool_calls']));self.assertNotIn('password=secret',message['content']);self.assertEqual(body['messages'][0]['content'],'password=secret')

    def test_paged_preview_preserves_all_large_file_text(self):
        raw='evidence line\n'*4000;fid=self.upload(raw);cfg=self.config(mask_sensitive=False,file_ids=[fid])
        code,preview=self.client.request('/api/context/preview',cfg);self.assertEqual(code,200,preview)
        source=next(s for s in preview['sources'] if s['id']=='file:'+fid);self.assertEqual(source['characters'],len(raw))
        pages=[];offset=0
        while offset is not None:
            code,page=self.client.request('/api/context/page',{'id':preview['id'],'config':cfg,'source_id':source['id'],'offset':offset});self.assertEqual(code,200,page);pages.append(page['text']);offset=page['next_offset']
        self.assertEqual(''.join(pages),raw)
