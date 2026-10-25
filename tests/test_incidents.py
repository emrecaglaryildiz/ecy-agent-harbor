"""Local log triage preserves evidence and never executes remediation."""
import os
os.environ['APP_MODE']='mock'
from app import server,incidents
import test_workspace as wh
import unittest


class IncidentTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    upload=wh.WorkspaceTests.upload
    project=wh.WorkspaceTests.project
    def setUp(self):
        with server.connect() as db:
            for table in ('incident_action_events','incident_actions','finding_reviews','finding_events','findings'):db.execute('DELETE FROM '+table)
        wh.WorkspaceTests.setUp(self)
    def finding(self):
        code,r=self.client.request('/api/platform/findings',{'title':'Investigate','details':'Evidence','severity':'info','status':'new'})
        self.assertEqual(code,201,r);return r['id']
    def test_timestamp_dedup_and_exact_ids_are_preserved(self):
        fid=self.upload('2026-10-25T12:00:00+03:00 ERROR user=alice ip=1.2.3.4\n2026-10-25T09:01:00Z ERROR user=alice ip=1.2.3.4\n2026-10-25T09:01:01Z ERROR user=bob ip=1.2.3.4\n')
        with server.connect() as db:d=incidents.window(db,None,fid)
        self.assertEqual(len(d['groups']),2);g=d['groups'][0];self.assertEqual(g['lines'],[1,2]);self.assertEqual(g['first_at'],'2026-10-25T09:00:00+00:00');self.assertEqual(g['last_at'],'2026-10-25T09:01:00+00:00');self.assertEqual(g['level'],'error')
    def test_naive_timestamps_never_get_an_assumed_timezone(self):
        fid=self.upload('2026-10-25 12:00:00 INFO hello\n2026-10-25T12:00:01Z INFO hello\n')
        with server.connect() as db:g=incidents.window(db,None,fid)['groups'][0]
        self.assertEqual(g['untimed'],1);self.assertEqual(g['lines'],[1,2]);self.assertEqual(g['first_at'],'2026-10-25T12:00:01+00:00')
    def test_pagination_counts_are_local_to_visible_window(self):
        fid=self.upload('same\n'*2001)
        with server.connect() as db:
            first=incidents.window(db,None,fid);second=incidents.window(db,None,fid,1000);third=incidents.window(db,None,fid,2000)
        self.assertEqual(first['groups'][0]['lines'],list(range(1,1001)));self.assertEqual(first['next_offset'],1000);self.assertEqual(second['next_offset'],2000);self.assertEqual(third['scanned'],1);self.assertIsNone(third['next_offset'])
    def test_unicode_lines_match_source_line_numbering(self):
        fid=self.upload('first\u2028second\rthird\r\nfourth\n')
        with server.connect() as db:d=incidents.window(db,None,fid)
        self.assertEqual([g['lines'][0] for g in d['groups']],[1,2,3,4]);self.assertEqual(d['scanned'],4)
    def test_cross_project_source_and_invalid_window_denied(self):
        pid=self.project();fid=self.upload(project=pid)
        self.assertEqual(self.client.request('/api/incidents/log?file_id='+fid)[0],400)
        self.assertEqual(self.client.request('/api/incidents/log?project_id='+pid+'&file_id='+fid+'&limit=2001')[0],400)
        self.assertEqual(self.client.request('/api/incidents/log?project_id='+pid+'&file_id='+fid)[0],200)
    def test_human_confidence_and_actions_are_owner_guarded(self):
        fid=self.finding();self.client.login('viewer')
        self.assertEqual(self.client.request('/api/incidents/confidence',{'finding_id':fid,'confidence':'high'})[0],403)
        self.client.login('operator');self.assertEqual(self.client.request('/api/incidents/actions',{'finding_id':fid,'title':'x'})[0],403)
        self.client.login('admin');self.assertEqual(self.client.request('/api/incidents/confidence',{'finding_id':fid,'confidence':'high'})[0],200)
        self.assertEqual(self.client.request('/api/incidents/confidence',{'finding_id':fid,'confidence':'certain'})[0],400)
        _,d=self.client.request('/api/incidents');self.assertEqual(d['reviews'][0]['confidence'],'high')
    def test_action_requires_approval_then_explicit_completion(self):
        fid=self.finding();code,a=self.client.request('/api/incidents/actions',{'finding_id':fid,'title':'Review firewall rules'});self.assertEqual(code,200,a)
        self.assertEqual(a['status'],'proposed')
        path='/api/incidents/actions/transition';ident=a['id']
        self.assertEqual(self.client.request(path,{'id':ident,'status':'done','expected_status':'proposed'})[0],400)
        self.assertEqual(self.client.request(path,{'id':ident,'status':'approved','expected_status':'proposed'})[0],200)
        self.assertEqual(self.client.request(path,{'id':ident,'status':'approved','expected_status':'proposed'})[0],400)
        self.assertEqual(self.client.request(path,{'id':ident,'status':'done','expected_status':'approved'})[0],200)
        _,d=self.client.request('/api/incidents');self.assertEqual([h['status'] for h in d['actions'][0]['history']],['proposed','approved','done'])
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def test_excerpt_is_explicit_and_original_remains_unchanged(self):
        source='x'*2000;fid=self.upload(source)
        with server.connect() as db:
            d=incidents.window(db,None,fid);self.assertTrue(d['groups'][0]['excerpt_shortened']);self.assertEqual(len(d['groups'][0]['excerpt']),1000)
            self.assertEqual(db.execute('SELECT text FROM files WHERE id=?',(fid,)).fetchone()[0],source)
