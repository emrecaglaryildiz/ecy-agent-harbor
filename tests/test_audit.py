"""Administrative audit coverage: content exclusion, atomicity and explicit trust limits."""
import concurrent.futures
import os
os.environ['APP_MODE']='mock'
os.environ['MOCK_DELAY']='0'
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import urllib.request
from app import server,audit,audit_verify
import test_workspace as wh
import test_harbor as helpers


class AuditTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    config=wh.WorkspaceTests.config
    def setUp(self):
        wh.WorkspaceTests.setUp(self)
        with server.connect() as db:self.start=audit.checkpoint(db)['seq']
    def events(self):return self.client.request('/api/audit?limit=100')[1]['items']

    def test_content_is_excluded_and_request_correlation_is_server_generated(self):
        body={'name':'DO_NOT_AUDIT_NAME','instruction':'sk-DO_NOT_AUDIT_SECRET password=TOP_SECRET_VALUE','api_key':'IGNORED_SECRET'}
        req=urllib.request.Request(helpers.BASE+'/api/projects',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':self.client.csrf,'Origin':helpers.BASE,'X-Request-ID':'untrusted-request-id'})
        with self.client.opener.open(req) as response:
            rid=response.headers['X-Request-ID'];project=json.load(response)
        self.assertRegex(rid,r'^[a-f0-9]{32}$')
        row=next(e for e in self.events() if e['request_id']==rid)
        self.assertEqual(row['actor_id'],'admin');self.assertEqual(row['target_id'],project['id']);self.assertEqual(row['route'],'/api/projects')
        encoded=json.dumps(self.events());self.assertNotIn('DO_NOT_AUDIT',encoded);self.assertNotIn('TOP_SECRET_VALUE',encoded);self.assertNotIn('IGNORED_SECRET',encoded)

    def test_admin_only_read_export_verify_and_checkpoint(self):
        for role in ('operator','viewer'):
            self.client.login(role)
            for path in ('/api/audit','/api/audit/export','/api/audit/verify','/api/audit/checkpoint'):self.assertEqual(self.client.request(path)[0],403,(role,path))
        self.assertEqual(helpers.Client().request('/api/audit')[0],401)

    def test_membership_event_identifies_only_project_account_and_grant(self):
        code,p=self.client.request('/api/projects',{'name':'Sensitive name','instruction':'Sensitive instructions'});self.assertEqual(code,201,p)
        self.assertEqual(self.client.request('/api/projects/members',{'project_id':p['id'],'user_id':'operator','access':'read'})[0],200)
        row=next(e for e in self.events() if e['event']=='project_membership_changed')
        self.assertEqual(row['target_id'],'project:'+p['id']+'/member:operator/access:read')
        self.assertNotIn('Sensitive',json.dumps(row))

    def test_filters_and_pagination_are_stable(self):
        for n in range(5):self.assertEqual(self.client.request('/api/projects',{'name':'Fixture '+str(n),'instruction':''})[0],201)
        code,one=self.client.request('/api/audit?event=workspace_updated&actor=admin&limit=2');self.assertEqual(code,200,one)
        code,two=self.client.request('/api/audit?event=workspace_updated&actor=admin&limit=2&before='+str(one['next_before']));self.assertEqual(code,200,two)
        self.assertFalse({e['seq'] for e in one['items']} & {e['seq'] for e in two['items']})
        self.assertTrue(all(e['event']=='workspace_updated' and e['actor_id']=='admin' for e in one['items']+two['items']))
        for q in ('limit=101','before=0','event=not-real','actor=not-real','request_id=secret'):self.assertEqual(self.client.request('/api/audit?'+q)[0],400,q)

    def test_task_history_deletion_retains_audit_evidence(self):
        tid=server.create_task(self.user,self.config())
        with server.connect() as db:
            seq=db.execute("SELECT seq FROM audit_events WHERE task_id=? AND event='queued'",(tid,)).fetchone()[0]
            db.execute("UPDATE tasks SET status='completed' WHERE id=?",(tid,))
        self.assertEqual(self.client.request('/api/history/delete',{'ids':[tid]})[0],200)
        with server.connect() as db:
            self.assertIsNone(db.execute('SELECT id FROM tasks WHERE id=?',(tid,)).fetchone())
            self.assertIsNotNone(db.execute('SELECT seq FROM audit_events WHERE seq=?',(seq,)).fetchone())
            self.assertEqual(db.execute('SELECT event FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone()[0],'history_deleted')

    def test_full_export_has_snapshot_and_offline_verifies(self):
        with self.client.opener.open(helpers.BASE+'/api/audit/export',timeout=5) as response:
            self.assertEqual(response.status,200);data=response.read()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'audit.jsonl';path.write_bytes(data)
            count=audit_verify.verify(path)
            self.assertGreaterEqual(count,self.start)
        self.assertEqual(self.client.request('/api/audit/delete',{'all':True})[0],404)


class AuditChainTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'audit.db'
        self.db=sqlite3.connect(self.path);self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close);audit.initialize(self.db);self.db.commit()
    def append(self):
        with self.db:audit.emit(self.db,'workspace_updated')
    def export(self,path):
        path.write_text(json.dumps({'type':'checkpoint',**audit.checkpoint(self.db)})+'\n'+''.join(json.dumps({'type':'event',**dict(r)})+'\n' for r in self.db.execute('SELECT * FROM audit_events ORDER BY seq')))

    def test_rollback_and_normal_sql_append_only_guards(self):
        self.append()
        with self.assertRaises(RuntimeError):
            with self.db:audit.emit(self.db,'workspace_updated');raise RuntimeError('rollback')
        self.assertEqual(audit.checkpoint(self.db)['seq'],1)
        for statement in ('DELETE FROM audit_events',"UPDATE audit_events SET actor_id='viewer'"):
            with self.assertRaises(sqlite3.IntegrityError):self.db.execute(statement)
        self.assertTrue(audit.verify_chunk(self.db,0,1)['valid'])

    def test_concurrent_appends_serialize_without_chain_forks(self):
        def write(index):
            token=audit.CONTEXT.set({'actor_id':'admin' if index%2 else 'operator','request_id':format(index,'032x'),'route':'/api/projects','target_id':''})
            try:
                with sqlite3.connect(self.path,timeout=10) as db:audit.emit(db,'workspace_updated')
            finally:audit.CONTEXT.reset(token)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(write,range(32)))
        self.assertEqual(audit.checkpoint(self.db)['seq'],32);self.assertTrue(audit.verify_chunk(self.db,0,32)['valid'])

    def test_tampering_and_incomplete_export_are_detected(self):
        self.append();self.append();path=Path(self.temp.name)/'export.jsonl';self.export(path)
        self.db.execute('DROP TRIGGER audit_no_update');self.db.execute("UPDATE audit_events SET actor_id='viewer' WHERE seq=1");self.db.commit()
        self.assertFalse(audit.verify_chunk(self.db,0,2)['valid'])
        path.write_text('\n'.join(path.read_text().splitlines()[:-1])+'\n')
        with self.assertRaises(ValueError):audit_verify.verify(path)

    def test_recomputed_local_chain_needs_independent_checkpoint(self):
        self.append();self.append();path=Path(self.temp.name)/'export.jsonl';anchor=Path(self.temp.name)/'checkpoint.json';self.export(path);anchor.write_text(json.dumps(audit.checkpoint(self.db)))
        records=[json.loads(line) for line in path.read_text().splitlines()];previous=audit.ZERO
        for row in records[1:]:row['actor_id']='viewer';row['previous_hash']=previous;row['hash']=audit.digest(row);previous=row['hash']
        records[0]['hash']=previous;path.write_text(''.join(json.dumps(row)+'\n' for row in records))
        self.assertEqual(audit_verify.verify(path),2)
        with self.assertRaises(ValueError):audit_verify.verify(path,anchor)

    def test_verification_is_paged_and_bounded(self):
        for n in range(501):audit.emit(self.db,'workspace_updated')
        self.db.commit();first=audit.verify_chunk(self.db,0,501);self.assertEqual(first['checked'],500);self.assertEqual(first['next_after'],500)
        self.assertIsNone(audit.verify_chunk(self.db,500,501)['next_after'])

    def test_older_independent_checkpoint_validates_a_newer_export_prefix(self):
        self.append();anchor=Path(self.temp.name)/'old-checkpoint.json';anchor.write_text(json.dumps(audit.checkpoint(self.db)))
        self.append();path=Path(self.temp.name)/'newer.jsonl';self.export(path)
        self.assertEqual(audit_verify.verify(path,anchor),2)
