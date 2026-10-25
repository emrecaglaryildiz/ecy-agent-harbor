"""Security regressions use disposable data and local sockets, never live services."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
from urllib.error import HTTPError
from app import server,security,mfa,pdf_service
import test_harbor as helpers
import test_workspace as wh


class SecurityTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    def setUp(self):
        wh.WorkspaceTests.setUp(self)
        with server.connect() as db:db.execute('DELETE FROM credentials');db.execute('DELETE FROM security_mfa')
    def request(self,path,body,headers=None):
        request=urllib.request.Request(helpers.BASE+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**(headers or {})})
        try:response=urllib.request.urlopen(request)
        except HTTPError as e:response=e
        return response.code,json.loads(response.read())
    def test_bootstrap_requires_secret_even_spoofed_host_and_forwarded(self):
        with patch.object(server,'MODE','live'):
            for headers in ({'Host':'localhost'},{'Host':'localhost','X-Forwarded-For':'127.0.0.1','Forwarded':'for=127.0.0.1'}):
                code,_=self.request('/api/setup',{'password':'disposable-test-pass'},headers);self.assertEqual(code,403)
            code,_=self.request('/api/setup',{'password':'disposable-test-pass','bootstrap_token':security.bootstrap_token(server)},{'Host':'localhost'})
            self.assertEqual(code,201)
            self.assertFalse((Path(server.DB_PATH).parent/'bootstrap.token').exists())
    def test_completed_setup_never_hashes_again(self):
        server.set_password('admin','disposable-test-pass')
        with patch.object(server,'MODE','live'),patch.object(server,'password_record',side_effect=AssertionError('must not hash')):
            code,_=self.request('/api/setup',{'password':'disposable-test-pass'});self.assertEqual(code,403)
            with self.assertRaises(ValueError):server.set_password('admin','disposable-test-pass',first_setup=True)
        with patch.dict(os.environ,{'AUTH_ADMIN_TOKEN':'explicit-test-token'}),patch.object(server,'MODE','live'),patch.object(server,'password_record',side_effect=AssertionError('must not hash')):
            with self.assertRaises(ValueError):server.set_password('admin','disposable-test-pass',first_setup=True)
    def test_public_mode_rejects_setup_and_insecure_startup(self):
        with patch.dict(os.environ,{'EXPOSURE_MODE':'public','PUBLIC_ORIGIN':'https://harbor.example','COOKIE_SECURE':'1'}):
            code,_=self.request('/api/setup',{'password':'disposable-test-pass'},{'Host':'harbor.example'});self.assertEqual(code,403)
            with self.assertRaises(ValueError):security.validate_public(server)
    def test_request_framing_and_unauthenticated_upload(self):
        url=helpers.BASE.replace('http://','');host,port=url.split(':')
        for raw in (b'Content-Length: 2\r\nContent-Length: 2',b'Content-Length: 2\r\nTransfer-Encoding: chunked'):
            s=socket.create_connection((host,int(port)));s.sendall(b'POST /api/login HTTP/1.0\r\nHost: localhost\r\nContent-Type: application/json\r\n'+raw+b'\r\n\r\n{}');answer=s.recv(2048);s.close();self.assertIn(b'400',answer)
        s=socket.create_connection((host,int(port)));s.settimeout(2);s.sendall(b'POST /api/files HTTP/1.0\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 1000000\r\n\r\n');answer=s.recv(2048);s.close();self.assertIn(b'403',answer)
    def test_bounded_ingress_and_slow_header_deadline_recover(self):
        with patch.dict(os.environ,{'HTTP_MAX_CONNECTIONS':'1','HTTP_HEADER_DEADLINE':'.2','HTTP_READ_TIMEOUT':'1'}):
            httpd=security.BoundedHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=httpd.serve_forever,daemon=True).start()
            try:
                slow=socket.create_connection(httpd.server_address);slow.sendall(b'GET /api/health HTTP/1.0\r\nHo');slow.settimeout(2)
                time.sleep(.4);answer=slow.recv(2048);slow.close();self.assertTrue(not answer or b'400' in answer)
                with urllib.request.urlopen('http://127.0.0.1:'+str(httpd.server_port)+'/api/health') as r:self.assertEqual(r.status,200)
            finally:httpd.shutdown();httpd.server_close()
    def test_mfa_replay_recovery_and_public_gate(self):
        password='disposable-test-pass';server.set_password('admin',password);self.client.login('admin')
        code,begun=self.client.request('/api/security/mfa/begin',{'password':password,'user':'admin'});self.assertEqual(code,200,begun)
        counter=int(time.time()//30)
        code,confirmed=self.client.request('/api/security/mfa/confirm',{'password':password,'code':mfa.code(begun['secret'],counter)});self.assertEqual(code,200,confirmed)
        self.assertFalse(mfa.verify_login(server,'admin',mfa.code(begun['secret'],counter)))
        self.assertTrue(mfa.verify_login(server,'admin',confirmed['recovery_codes'][0]))
        self.assertFalse(mfa.verify_login(server,'admin',confirmed['recovery_codes'][0]))
        with patch.dict(os.environ,{'EXPOSURE_MODE':'public','PUBLIC_ORIGIN':'https://harbor.example','COOKIE_SECURE':'1','PDF_UPLOADS':'disabled'}),patch.object(server,'MODE','live'):
            security.validate_public(server)
        with server.connect() as db:
            value=db.execute('SELECT secret,recovery FROM security_mfa').fetchone();self.assertNotIn(begun['secret'],value[0]);self.assertNotIn(confirmed['recovery_codes'][1],value[1])
    def test_pdf_timeout_reaps_worker_and_recovers_slot(self):
        import subprocess
        class Fake:
            pid=999999999;returncode=None;stdin=None
            def communicate(self,*a,**kw):
                if kw:raise subprocess.TimeoutExpired('worker',25)
                self.returncode=-9;return (None,None)
            def poll(self):return self.returncode
            def wait(self,**kw):self.returncode=-9
        with patch.object(pdf_service.subprocess,'Popen',return_value=Fake()),patch.object(pdf_service.os,'killpg') as kill:
            with self.assertRaises(ValueError):pdf_service.extract(b'%PDF-fake')
            self.assertTrue(kill.called)
        self.assertTrue(pdf_service.SLOTS.acquire(blocking=False));pdf_service.SLOTS.release()
    def test_external_image_csp_has_no_remote_allowance(self):
        with urllib.request.urlopen(helpers.BASE+'/') as response:
            policy=response.headers['Content-Security-Policy'];self.assertIn("img-src 'self' data:;",policy);self.assertNotIn('data: https:',policy)

    def test_public_mode_cannot_add_or_login_password_only_role(self):
        password='disposable-test-pass';server.set_password('admin',password);self.client.login('admin')
        with patch.dict(os.environ,{'EXPOSURE_MODE':'public','PUBLIC_ORIGIN':'https://harbor.example','COOKIE_SECURE':'1'}):
            request=urllib.request.Request(helpers.BASE+'/api/access/password',data=json.dumps({'user':'viewer','password':password}).encode(),headers={'Host':'harbor.example','Content-Type':'application/json','X-CSRF-Token':self.client.csrf})
            try:response=self.client.opener.open(request)
            except HTTPError as exc:response=exc
            self.assertEqual(response.code,400);response.close()
            self.assertFalse(mfa.verify_login(server,'viewer',''))
