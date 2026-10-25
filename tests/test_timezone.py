import sqlite3
import unittest
from unittest.mock import patch
from app.time_utils import day_window, local_day, display_time
from app import server


class IstanbulTimeTests(unittest.TestCase):
    def test_midnight_boundary(self):
        self.assertEqual(local_day('2026-10-24T20:59:59Z'), '2026-10-24')
        self.assertEqual(local_day('2026-10-24T21:00:00Z'), '2026-10-25')
        self.assertEqual(day_window('2026-10-25T00:00:00Z'), ('2026-10-25', '2026-10-24T21:00:00+00:00', '2026-10-25T21:00:00+00:00'))
        self.assertEqual(display_time('2026-10-24T22:05:00Z'), '2026-10-25T01:05:00+03:00')

    def test_usage_is_local_day_with_exclusive_end_and_preserved_totals(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('CREATE TABLE tasks(created_at TEXT,cost REAL); CREATE TABLE deleted_costs(day TEXT,amount REAL,day_timezone TEXT);')
        db.executemany('INSERT INTO tasks VALUES(?,?)', [('2026-10-24T20:59:59Z',1), ('2026-10-24T21:00:00Z',2), ('2026-10-25T23:00:00+03:00',4), ('2026-10-25T21:00:00Z',8)])
        db.execute("INSERT INTO deleted_costs VALUES('2026-10-25',3,'Europe/Istanbul')")
        with patch.object(server, 'now', return_value='2026-10-25T01:00:00Z'):
            usage = server.usage_today(db)
        self.assertEqual(usage['cost'],9)
        self.assertEqual(usage['total_cost'],18)
        self.assertFalse(usage['legacy_daily_estimate'])
