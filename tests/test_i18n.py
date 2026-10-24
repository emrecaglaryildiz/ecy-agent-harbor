"""Localized metadata/errors preserve user and model contents."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

os.environ['APP_MODE']='mock'
from app import server
from app.localization import language, translate
from app.exports import markdown_report, export_report
import test_harbor as helpers


class LocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();server.DB_PATH=str(Path(cls.temp.name)/'i18n.db');server.initialize()
        cls.httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);helpers.BASE=f'http://127.0.0.1:{cls.httpd.server_port}'
        threading.Thread(target=cls.httpd.serve_forever,daemon=True).start();cls.client=helpers.Client();cls.client.login('admin')
    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close();cls.temp.cleanup()

    def test_language_policy_and_legacy_api(self):
        self.assertEqual(language('tr-TR,tr;q=0.9'),'tr');self.assertEqual(language('en-US,en;q=0.9'),'en');self.assertEqual(language('de-DE'),'en');self.assertEqual(language(None),'tr')

    def test_catalog_and_script_served_locally(self):
        code,catalog=self.client.request('/locales/en.json');self.assertEqual(code,200);self.assertEqual(catalog['Ajan Alanı'],'Agent Workspace')
        with urllib.request.urlopen(helpers.BASE+'/i18n.js') as response:self.assertEqual(response.status,200)
        with urllib.request.urlopen(helpers.BASE+'/brand.svg') as response:self.assertEqual(response.headers.get_content_type(),'image/svg+xml');self.assertIn(b'<svg',response.read())

    def test_english_api_error(self):
        req=urllib.request.Request(helpers.BASE+'/api/tasks',data=json.dumps({'prompt':''}).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':self.client.csrf,'Accept-Language':'en'})
        try:r=self.client.opener.open(req)
        except urllib.error.HTTPError as exc:r=exc
        self.assertEqual(r.code,400);self.assertEqual(json.loads(r.read())['error'],'Context must contain 1–16,000 characters')

    def test_turkish_legacy_api_error(self):
        code,result=self.client.request('/api/tasks',{'prompt':''});self.assertEqual(code,400);self.assertEqual(result['error'],'Bağlam 1–16.000 karakter olmalı')

    def test_report_metadata_localized_but_outputs_unchanged(self):
        task=dict(id='abc',title='Özgün görev adı',prompt='Türkçe kullanıcı promptu',created_at='2026-10-25',status='completed',cost=0,language='en')
        run=dict(id='r',agent_id='local',kind='participant',status='completed',input_tokens=1,output_tokens=1,finish_reason='stop',output='Türkçe özgün model yanıtı')
        en=markdown_report(task,[run],server.BY_ID);self.assertIn('Task report',en);self.assertIn('**Status:** Completed',en);self.assertIn(run['output'],en);self.assertIn(task['prompt'],en)
        tr=markdown_report(dict(task,language='tr'),[run],server.BY_ID);self.assertIn('Görev raporu',tr);self.assertIn(run['output'],tr)
        data,mime=export_report(task,[run],'pdf',server.BY_ID);self.assertEqual(mime,'application/pdf');self.assertTrue(data.startswith(b'%PDF'))

    def test_new_task_language_and_invalid_language_rollback(self):
        body=dict(prompt='Inspect',mode='single',agents=['local'],language='en')
        tid=server.create_task({'id':'admin','name':'Emre','role':'admin'},body)
        with server.connect() as db:self.assertEqual(db.execute('SELECT language FROM tasks WHERE id=?',(tid,)).fetchone()[0],'en');before=db.execute('SELECT count(*) FROM tasks').fetchone()[0]
        with self.assertRaises(ValueError):server.create_task({'id':'admin','name':'Emre','role':'admin'},dict(body,language='de'))
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],before)

    def test_unknown_error_has_english_fallback_without_exposing_message(self):
        self.assertEqual(translate('provider raw payload secret','en',error=True),'Could not complete the operation. Check your input, permissions, or connection.')

    def test_continuation_retry_and_followup_keep_selected_language(self):
        tid=server.create_task({'id':'admin','name':'Emre','role':'admin'},dict(prompt='Özgün görev',mode='single',agents=['local']))
        rid='language-'+tid
        with server.connect() as db:
            db.execute("UPDATE tasks SET status='incomplete' WHERE id=?",(tid,))
            db.execute("INSERT INTO runs(id,task_id,agent_id,kind,status,output,finish_reason,created_at,request_state) VALUES(?,?,?,'participant','incomplete',?,'length',?,?)",(rid,tid,'local','Özgün yanıt',server.now(),json.dumps({'prompt':'Özgün görev','context':''})))
        for endpoint,body in [('continue',{'run_id':rid,'language':'en'}),('retry',{'run_id':rid,'language':'en'}),('followup',{'prompt':'Explain the evidence','language':'en'})]:
            code,data=self.client.request('/api/tasks/'+tid+'/'+endpoint,body)
            self.assertEqual(code,201,data)
            with server.connect() as db:self.assertEqual(db.execute('SELECT language FROM tasks WHERE id=?',(data['id'],)).fetchone()[0],'en')
