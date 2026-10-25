"""Minimal, credential-free probes; readiness never calls providers or creates a DB."""
from pathlib import Path
import sqlite3
import time


def ready(server):
    if server.STOP.is_set():
        return False
    deadline=time.monotonic()+.5
    try:
        # mode=ro is deliberate: a mistyped/deleted path must not create a database.
        uri=Path(server.DB_PATH).resolve().as_uri()+'?mode=ro'
        db=sqlite3.connect(uri,uri=True,timeout=.25)
        try:
            db.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
            db.execute('PRAGMA query_only=ON')
            for table in ('users','tasks','runs'):
                db.execute('SELECT id FROM '+table+' LIMIT 1').fetchone()
        finally:
            db.close()
        return not server.STOP.is_set()
    except (OSError,sqlite3.Error,ValueError):
        return False
