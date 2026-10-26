"""Storage and read-only retention checks use synthetic evidence in disposable directories."""
import os
os.environ['APP_MODE']='mock'
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from app import server, storage, lifecycle
import test_workspace as wh


class StoreTests(unittest.TestCase):
    def test_exclusive_publication_and_atomic_report_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=storage.LocalStore(Path(tmp)/'db');fid='a'*32
            store.write('uploads',fid,b'original')
            with self.assertRaises(FileExistsError):store.write('uploads',fid,b'overwrite')
            self.assertEqual(store.read('uploads',fid),b'original')
            store.write('artifacts',fid,b'old',replace=True)
            with patch('app.storage.os.replace',side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):store.write('artifacts',fid,b'partial',replace=True)
            self.assertEqual(store.read('artifacts',fid),b'old')
            self.assertFalse(list(store.folder('artifacts').glob('.pending-*')))
            store.write('artifacts',fid,b'new',replace=True)
            self.assertEqual(store.read('artifacts',fid),b'new')
            if os.name!='nt':self.assertEqual(store.path('uploads',fid).stat().st_mode&0o777,0o600)

    def test_invalid_ids_and_symlinks_never_read_or_overwrite_external_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=storage.LocalStore(Path(tmp)/'db');outside=Path(tmp)/'secret';outside.write_bytes(b'sensitive')
            for bad in ('../secret','a'*31,'A'*32,'x/y',None):
                with self.assertRaises(ValueError):store.read('uploads',bad)
            folder=store.folder('uploads',True);(folder/('a'*32)).symlink_to(outside)
            for call in (lambda:store.read('uploads','a'*32),lambda:store.write('uploads','a'*32,b'bad'),lambda:store.remove('uploads','a'*32)):
                with self.assertRaises(ValueError):call()
            self.assertEqual(outside.read_bytes(),b'sensitive')
            (Path(tmp)/'artifacts').symlink_to(folder,target_is_directory=True)
            with self.assertRaises(ValueError):store.write('artifacts','b'*32,b'bad')


class LifecycleTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    upload=wh.WorkspaceTests.upload
    config=wh.WorkspaceTests.config
    project=wh.WorkspaceTests.project
    def setUp(self):
        wh.WorkspaceTests.setUp(self)
        # Old evidence bytes from previous test methods are intentionally removed only here.
        store=storage.LocalStore(server.DB_PATH)
        for kind in ('uploads','artifacts'):
            for p in store.folder(kind,True).iterdir():p.unlink()
    def closed(self,project=None,status='completed',updated='2000-01-01T00:00:00+00:00',cost=0.25):
        tid=server.create_task(self.user,self.config(project_id=project))
        with server.connect() as db:db.execute('UPDATE tasks SET status=?,updated_at=?,cost=? WHERE id=?',(status,updated,cost,tid))
        return tid

    def test_inventory_counts_discrepancies_without_contents_or_cleanup(self):
        fid=self.upload('synthetic secret evidence');store=storage.LocalStore(server.DB_PATH)
        store.path('uploads',fid).write_bytes(b'x')
        store.write('uploads','e'*32,b'orphan sensitive bytes')
        (store.folder('uploads')/'unknown-name').write_text('private')
        tid=self.closed();store.write('artifacts',tid,b'private report')
        with server.connect() as db:
            with patch('app.storage.LocalStore.read',side_effect=AssertionError('No content reads')):
                r=storage.inventory(db,server.DB_PATH)
        self.assertEqual(r['stores']['uploads']['size_mismatches'],1)
        self.assertEqual(r['stores']['uploads']['unreferenced'],1)
        self.assertEqual(r['stores']['uploads']['unsafe_entries'],1)
        self.assertEqual(r['stores']['artifacts']['files'],1)
        self.assertNotIn('private',json.dumps(r));self.assertFalse(r['hashes_checked'])
        self.assertTrue((store.folder('uploads')/'unknown-name').exists())
        store.remove('uploads',fid)
        with server.connect() as db:self.assertEqual(storage.inventory(db,server.DB_PATH)['stores']['uploads']['missing'],1)

    def test_inventory_is_bounded_and_does_not_create_directories(self):
        with tempfile.TemporaryDirectory() as tmp,server.connect() as db:
            store=storage.LocalStore(Path(tmp)/'absent.db');storage.inventory(db,Path(tmp)/'absent.db')
            self.assertFalse(store.folder('uploads').exists())
            for index in range(4):store.write('uploads',format(index,'032x'),b'x')
            result=storage.inventory(db,Path(tmp)/'absent.db',scan_limit=2)
            self.assertTrue(result['truncated']);self.assertEqual(result['stores']['uploads']['files'],2)

    def test_retention_excludes_open_reserved_recent_and_other_projects(self):
        project=self.project();eligible=self.closed(project);other=self.closed();recent=self.closed(project,updated='2999-01-01T00:00:00+00:00')
        queued=self.closed(project,status='queued');reserved=self.closed(project)
        running=self.closed(project)
        with server.connect() as db:
            db.execute('UPDATE tasks SET reserved=1 WHERE id=?',(reserved,))
            db.execute("INSERT INTO runs(id,task_id,agent_id,kind,status,created_at) VALUES(?,?,'local','participant','running',?)",(running+'r',running,server.now()))
            before=db.total_changes
            report=lifecycle.preview(db,self.user,{'project_id':project,'days':90},server)
            self.assertEqual(db.total_changes,before)
        self.assertEqual([r['id'] for r in report['items']],[eligible]);self.assertEqual(report['total'],1)
        self.assertEqual(report['cost_preserved_usd'],0.25);self.assertTrue(report['preview_only']);self.assertFalse(report['automatic_deletion'])
        self.assertNotIn('Kanıtları',json.dumps(report))
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],6)

    def test_retention_uses_actual_instants_and_last_update(self):
        old=self.closed(updated='2026-01-01T02:00:00+03:00');recent=self.closed(updated='2026-01-01T01:00:00-03:00')
        with server.connect() as db,patch.object(server,'now',return_value='2026-01-02T00:00:00+00:00'):
            r=lifecycle.preview(db,self.user,{'days':1},server)
        self.assertEqual([i['id'] for i in r['items']],[old])

    def test_preview_pagination_keeps_every_candidate_without_source_text(self):
        for _ in range(103):self.closed()
        code,first=self.client.request('/api/platform/retention/preview',{'days':90})
        self.assertEqual(code,200);self.assertEqual(len(first['items']),100);self.assertEqual(first['next_offset'],100)
        code,last=self.client.request('/api/platform/retention/preview',{'days':90,'offset':100})
        self.assertEqual(len(last['items']),3);self.assertIsNone(last['next_offset'])
        self.assertEqual(len({i['id'] for i in first['items']+last['items']}),103)
        self.assertEqual(set(first['items'][0]),{'id','status','updated_at'})

    def test_admin_only_endpoints_and_strict_validation(self):
        for role in ('operator','viewer'):
            self.client.login(role)
            self.assertEqual(self.client.request('/api/platform/storage')[0],403)
            self.assertEqual(self.client.request('/api/platform/retention/preview',{'days':90})[0],403)
        self.client.login('admin')
        self.assertEqual(self.client.request('/api/platform/storage')[0],200)
        for value in (True,0,-1,3651,'90',1.5):
            self.assertEqual(self.client.request('/api/platform/retention/preview',{'days':value})[0],400)
        self.assertEqual(self.client.request('/api/platform/retention/preview',{'project_id':'missing','days':90})[0],400)
        for offset in (True,-1,1000001,'0'):
            self.assertEqual(self.client.request('/api/platform/retention/preview',{'offset':offset})[0],400)
        self.assertEqual(self.client.request('/api/platform/retention/execute',{'days':90})[0],404)
