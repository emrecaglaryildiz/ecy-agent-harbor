"""Readiness checks are local, bounded and reveal no deployment details."""
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from app import health,server
import test_workspace as wh
import test_harbor as helpers


class HealthTests(unittest.TestCase):
    def test_missing_database_is_unready_and_never_created(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'missing.db';app=SimpleNamespace(DB_PATH=str(path),STOP=threading.Event())
            self.assertFalse(health.ready(app));self.assertFalse(path.exists())

    def test_schema_and_shutdown_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            app=SimpleNamespace(DB_PATH=str(Path(folder)/'health.db'),STOP=threading.Event())
            with sqlite3.connect(app.DB_PATH) as db:
                db.execute('CREATE TABLE unrelated(id TEXT)')
                self.assertFalse(health.ready(app))
                for table in ('users','tasks','runs'):db.execute('CREATE TABLE '+table+'(id TEXT)')
            self.assertTrue(health.ready(app));app.STOP.set();self.assertFalse(health.ready(app))

    def test_busy_database_returns_promptly(self):
        with tempfile.TemporaryDirectory() as folder:
            app=SimpleNamespace(DB_PATH=str(Path(folder)/'locked.db'),STOP=threading.Event())
            with sqlite3.connect(app.DB_PATH) as db:
                for table in ('users','tasks','runs'):db.execute('CREATE TABLE '+table+'(id TEXT)')
                db.commit();db.execute('BEGIN EXCLUSIVE')
                start=time.monotonic();self.assertFalse(health.ready(app));self.assertLess(time.monotonic()-start,1.5)


class HealthHTTPTests(unittest.TestCase):
    setUpClass=classmethod(wh.WorkspaceTests.setUpClass.__func__)
    tearDownClass=classmethod(wh.WorkspaceTests.tearDownClass.__func__)
    def setUp(self):
        stop=patch.object(server,'STOP',threading.Event());stop.start();self.addCleanup(stop.stop)

    def test_probes_are_credential_free_and_minimal(self):
        for path in ('live','ready'):
            with urlopen(helpers.BASE+'/api/health/'+path) as response:
                self.assertEqual(response.status,200);self.assertEqual(json.load(response),{'ok':True})
                self.assertEqual(response.headers['Cache-Control'],'no-store')
        previous=server.DB_PATH
        try:
            server.DB_PATH=str(Path(self.temp.name)/'unavailable.db')
            with self.assertRaises(HTTPError) as raised:urlopen(helpers.BASE+'/api/health/ready')
            self.assertEqual(raised.exception.code,503);self.assertEqual(json.load(raised.exception),{'ok':False})
            with urlopen(helpers.BASE+'/api/health/live') as response:self.assertEqual(response.status,200)
        finally:server.DB_PATH=previous
