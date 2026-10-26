"""Offline restore rehearsals use synthetic evidence and disposable encryption keys only."""
import os
os.environ['APP_MODE']='mock'
os.environ['MOCK_DELAY']='0'
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from cryptography.fernet import Fernet
from app import server,backup,audit
import test_workspace as wh


class RecoveryTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    config=wh.WorkspaceTests.config
    upload=wh.WorkspaceTests.upload
    password='disposable-recovery-password'
    def setUp(self):
        wh.WorkspaceTests.setUp(self)
        with server.connect() as db:
            db.execute('UPDATE dispatch_control SET paused=0 WHERE id=1');db.execute('DELETE FROM mcp_calls');db.execute('DELETE FROM mcp_servers');db.execute('DELETE FROM security_mfa')
        self.fid=self.upload('synthetic recovery evidence\n')
    def pause(self):
        code,data=self.client.request('/api/operations/dispatch',{'paused':True,'expected_paused':False});self.assertEqual(code,200,data)
    def create(self):return backup.create(server,self.password)
    def repack(self,content,change,inventory=True):
        with backup.unpack(content,self.password) as source:members={n:source.read(n) for n in source.namelist()}
        change(members);manifest=json.loads(members['manifest.json'])
        if inventory:manifest['integrity']={'algorithm':'sha256','members':{n:{'size':len(v),'sha256':hashlib.sha256(v).hexdigest()} for n,v in members.items() if n!='manifest.json'}}
        else:manifest.pop('integrity',None)
        members['manifest.json']=json.dumps(manifest).encode();out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
            for n,v in members.items():target.writestr(n,v)
        salt=os.urandom(16);return backup.MAGIC+salt+backup.cipher(self.password,salt).encrypt(out.getvalue())
    def add_secret(self):
        token=server.encrypt(server.DB_PATH,'synthetic-provider-secret')
        with server.connect() as db:
            cfg=json.loads(db.execute("SELECT config FROM agent_configs WHERE id='local'").fetchone()[0]);cfg['encrypted_key']=token;db.execute("UPDATE agent_configs SET config=? WHERE id='local'",(json.dumps(cfg),))

    def test_paused_queue_is_backed_up_and_restored_as_held_evidence(self):
        tid=server.create_task(self.user,self.config(file_ids=[self.fid]))
        self.assertFalse(self.client.request('/api/platform/backup/status')[1]['ready'])
        with self.assertRaises(ValueError):self.create()
        self.pause();status=self.client.request('/api/platform/backup/status')[1];self.assertTrue(status['ready']);self.assertEqual(status['queued'],1)
        content=self.create()
        with tempfile.TemporaryDirectory() as folder:
            target,report=backup.restore(content,self.password,Path(folder)/'restored',return_report=True)
            self.assertEqual(report['pending_tasks_held'],1)
            with sqlite3.connect(target/'harbor.db') as db:
                self.assertEqual(db.execute('SELECT paused FROM dispatch_control').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'incomplete')
                self.assertEqual(db.execute('SELECT event FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone()[0],'backup_restored')
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'queued')

    def test_inflight_runs_and_outbound_work_prevent_backup(self):
        tid=server.create_task(self.user,self.config());self.pause()
        with server.connect() as db:
            db.execute("UPDATE tasks SET status='completed' WHERE id=?",(tid,));db.execute("INSERT INTO runs(id,task_id,agent_id,kind,status,created_at) VALUES('fixture-running',?,'local','participant','running',?)",(tid,server.now()))
        with self.assertRaises(ValueError):self.create()
        with server.connect() as db:
            db.execute("UPDATE runs SET status='completed'");db.execute("INSERT INTO mcp_calls(id,server_id,tool,owner_id,arguments,status,revision,created_at,updated_at) VALUES('fixture','fixture','read','admin','{}','dispatched',1,?,?)",(server.now(),server.now()))
        with self.assertRaises(ValueError):self.create()
        with server.connect() as db:
            db.execute("UPDATE mcp_calls SET status='completed'");db.execute("INSERT INTO notifications VALUES('fixture',?,'sending',NULL,?,NULL)",(tid,server.now()))
        with self.assertRaises(ValueError):self.create()

    def test_partial_source_file_is_not_silently_archived(self):
        path=Path(server.DB_PATH).parent/'uploads'/self.fid;path.write_bytes(b'partial')
        with self.assertRaises(ValueError):self.create()

    def test_rehearsal_checks_secrets_without_network_or_live_data_mutation(self):
        self.add_secret();content=self.create()
        with server.connect() as db:head=audit.checkpoint(db)
        before=(Path(server.DB_PATH).parent/'uploads'/self.fid).read_bytes()
        with patch('socket.socket',side_effect=AssertionError('network prohibited')):report=backup.verify(content,self.password)
        self.assertTrue(report['verified']);self.assertTrue(report['inventory_verified']);self.assertEqual(report['files_verified'],1);self.assertEqual(report['encrypted_values_verified'],1)
        self.assertNotIn('synthetic-provider-secret',json.dumps(report));self.assertFalse(report['live_data_modified']);self.assertFalse(report['network_calls'])
        with server.connect() as db:self.assertEqual(audit.checkpoint(db),head)
        self.assertEqual((Path(server.DB_PATH).parent/'uploads'/self.fid).read_bytes(),before)

    def test_legacy_inventory_free_backups_remain_verifiable(self):
        legacy=self.repack(self.create(),lambda m:None,inventory=False)
        report=backup.verify(legacy,self.password);self.assertTrue(report['verified']);self.assertFalse(report['inventory_verified']);self.assertEqual(report['files_verified'],1)

    def test_missing_or_wrong_master_key_is_rejected(self):
        self.add_secret();content=self.create()
        missing=self.repack(content,lambda m:m.pop('master.key'))
        wrong=self.repack(content,lambda m:m.__setitem__('master.key',Fernet.generate_key()))
        for candidate in (missing,wrong):
            with self.assertRaises(ValueError):backup.verify(candidate,self.password)

    def test_file_hash_and_inventory_mismatch_are_rejected(self):
        content=self.create()
        bad_file=self.repack(content,lambda m:m.__setitem__('uploads/'+self.fid,b'changed evidence'))
        with self.assertRaises(ValueError):backup.verify(bad_file,self.password)
        with backup.unpack(content,self.password) as z:members={n:z.read(n) for n in z.namelist()}
        members['uploads/'+self.fid]=b'wrong';out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            for n,v in members.items():z.writestr(n,v)
        salt=os.urandom(16);bad=backup.MAGIC+salt+backup.cipher(self.password,salt).encrypt(out.getvalue())
        with self.assertRaises(ValueError):backup.unpack(bad,self.password)

    def test_corrupted_audit_chain_is_rejected_even_with_valid_archive_inventory(self):
        content=self.create()
        def corrupt(members):
            with tempfile.TemporaryDirectory() as folder:
                p=Path(folder)/'db';p.write_bytes(members['harbor.db'])
                with contextlib.closing(sqlite3.connect(p)) as db,db:db.execute('DROP TRIGGER audit_no_update');db.execute("UPDATE audit_events SET actor_id='viewer' WHERE seq=1")
                members['harbor.db']=p.read_bytes()
        with self.assertRaises(ValueError):backup.verify(self.repack(content,corrupt),self.password)

    def test_existing_and_concurrently_created_destinations_are_never_replaced(self):
        content=self.create()
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'destination';target.mkdir();marker=target/'keep';marker.write_text('original')
            with self.assertRaises(ValueError):backup.restore(content,self.password,target)
            self.assertEqual(marker.read_text(),'original')
            late=Path(folder)/'late';original=backup.validate_directory
            def create_late(root,db):
                report=original(root,db)
                if not late.exists():late.mkdir();(late/'keep').write_text('concurrent')
                return report
            with patch.object(backup,'validate_directory',side_effect=create_late):
                with self.assertRaises(ValueError):backup.restore(content,self.password,late)
            self.assertEqual((late/'keep').read_text(),'concurrent')

    def test_status_is_admin_only_and_cli_verify_prints_no_secret(self):
        self.client.login('operator');self.assertEqual(self.client.request('/api/platform/backup/status')[0],403)
        self.client.login('viewer');self.assertEqual(self.client.request('/api/platform/backup/status')[0],403)
        content=self.create()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'archive.ecybak';path.write_bytes(content);output=io.StringIO()
            with patch('sys.argv',['backup','verify',str(path)]),patch('app.backup.getpass.getpass',return_value=self.password),contextlib.redirect_stdout(output):backup.main()
            self.assertTrue(json.loads(output.getvalue())['verified']);self.assertNotIn(self.password,output.getvalue())
            self.assertEqual(list(Path(folder).iterdir()),[path])

    def test_restore_rejects_a_database_that_reenables_dispatch_during_suspension(self):
        content=self.create()
        def add_trigger(members):
            with tempfile.TemporaryDirectory() as folder:
                p=Path(folder)/'db';p.write_bytes(members['harbor.db'])
                with contextlib.closing(sqlite3.connect(p)) as db,db:
                    db.execute("CREATE TRIGGER restore_fixture AFTER UPDATE OF paused ON dispatch_control WHEN NEW.paused=1 BEGIN UPDATE dispatch_control SET paused=0 WHERE id=1; END")
                members['harbor.db']=p.read_bytes()
        candidate=self.repack(content,add_trigger)
        with self.assertRaises(ValueError):backup.verify(candidate,self.password)
