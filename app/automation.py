"""Durable schedules and opt-in Telegram outbox; uncertain deliveries are never replayed."""
import json
import time
import urllib.request
import urllib.error
import uuid


def schedule_tick(server, timestamp=None):
    stamp = time.time() if timestamp is None else timestamp
    with server.CONFIG_LOCK, server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        due = db.execute('SELECT * FROM schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run', (stamp,)).fetchall()
        for row in due:
            next_run = stamp + row['interval_minutes'] * 60
            db.execute('UPDATE schedules SET next_run=? WHERE id=?', (next_run, row['id']))
            active = db.execute("SELECT 1 FROM tasks WHERE id=? AND status IN ('queued','running','awaiting_approval')", (row['last_task'],)).fetchone()
            if active:
                continue
            owner = db.execute('SELECT * FROM users WHERE id=?', (row['owner_id'],)).fetchone()
            if not owner:
                db.execute("UPDATE schedules SET enabled=0,last_error='Sahip hesabı bulunamadı' WHERE id=?", (row['id'],))
                continue
            try:
                # Task creation, next due time and last_task commit together: one dispatch per interval.
                tid = server.create_task(dict(owner), json.loads(row['config']), db=db)
                db.execute('UPDATE schedules SET last_task=?,last_error=NULL WHERE id=?', (tid, row['id']))
                server.log(db, tid, 'scheduled', row['name'])
            except Exception:
                db.execute("UPDATE schedules SET enabled=0,last_error='Görev başlatılamadı; ajanları, dosyaları ve internet ayarını kontrol edip tekrar etkinleştir' WHERE id=?", (row['id'],))


def telegram_config(server, db):
    row = db.execute("SELECT config FROM integrations WHERE id='telegram'").fetchone()
    return json.loads(row[0]) if row else {}


def enqueue_notifications(server):
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cfg = telegram_config(server, db)
        if not cfg.get('enabled'):
            return
        for row in db.execute("SELECT id,project_id FROM tasks WHERE notified=0 AND updated_at>=? AND status IN ('completed','failed','incomplete','cancelled','awaiting_approval')", (cfg.get('enabled_since', server.now()),)).fetchall():
            if row['project_id'] and server.access.mode(db,row['project_id'])=='private':continue
            db.execute("INSERT INTO notifications VALUES(?,?,'pending',NULL,?,NULL)", (uuid.uuid4().hex, row['id'], server.now()))
            db.execute('UPDATE tasks SET notified=1 WHERE id=?', (row['id'],))


def send_one(server, opener=None):
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cfg = telegram_config(server, db)
        if not cfg.get('enabled'):
            return
        row = db.execute("SELECT * FROM notifications WHERE status='pending' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return
        task = db.execute('SELECT * FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
        if task['project_id'] and server.access.mode(db,task['project_id'])=='private':
            db.execute("UPDATE notifications SET status='skipped',error='Özel proje için ortak bildirim gönderilmedi' WHERE id=?",(row['id'],));return
        db.execute("UPDATE notifications SET status='sending' WHERE id=?", (row['id'],))
        summary = task['synthesis'] or next((r[0] for r in db.execute("SELECT output FROM runs WHERE task_id=? AND kind='participant' AND output IS NOT NULL ORDER BY created_at DESC", (task['id'],))), '')
    # Default notification contains only status and amount. Titles/output require an explicit setting.
    from .localization import translate
    en = task['language'] == 'en'
    status = {'completed': 'Completed', 'failed': 'Failed', 'incomplete': 'Incomplete', 'cancelled': 'Cancelled', 'awaiting_approval': 'Awaiting approval'}.get(task['status'], task['status']) if en else task['status']
    text = f"ECY Agent Harbor · {status}\n{'Task' if en else 'Görev'}: {task['id'][:8]}\n{'Usage' if en else 'Kullanım'}: ${task['cost']:.4f}"
    if cfg.get('include_summary'):
        text += '\n' + task['title'] + '\n\n' + (summary or task['error'] or '')[:1000]
    if cfg.get('origin'):
        text += '\n\n' + cfg['origin'] + '/?task=' + task['id']
    status, error = 'unknown', 'Telegram teslimatı doğrulanamadı. Tekrar gönderim kopya mesaj oluşturabilir.'
    try:
        token = server.decrypt(server.DB_PATH, cfg['token'])
        request = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage',
                    data=json.dumps({'chat_id': cfg['chat_id'], 'text': text}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        client = opener or urllib.request.build_opener(NoRedirect)
        with client.open(request, timeout=20) as response:
            result = json.loads(response.read())
        if result.get('ok') is True:
            status, error = 'sent', None
        else:
            status, error = 'failed', 'Telegram mesajı kabul etmedi; bot ve sohbet ayarını kontrol et'
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            status, error = 'failed', 'Telegram erişimi veya sohbet ayarı kabul edilmedi'
    except Exception:
        pass
    with server.connect() as db:
        db.execute('UPDATE notifications SET status=?,error=?,sent_at=? WHERE id=?', (status, error, server.now() if status == 'sent' else None, row['id']))


def loop(server):
    while not server.STOP.is_set():
        try:
            schedule_tick(server)
            enqueue_notifications(server)
            send_one(server)
        except Exception:
            # Credentials, provider payloads and bot URLs must never be logged.
            pass
        server.STOP.wait(5)
