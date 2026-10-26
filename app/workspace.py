"""Shared project workspace, reusable tasks, evidence and persisted automation."""
import json
import re
import sys
import time
import uuid
from urllib.parse import parse_qs, urlparse
from . import files, access

BUILTINS = [
    {'id': 'security-log', 'name': 'Şüpheli log incele', 'description': 'Dosya ve satır kanıtıyla olay incelemesi', 'builtin': True,
     'config': {'prompt': 'Yüklenen logları incele. Zaman çizelgesi, şüpheli olaylar, yanlış pozitif olasılıkları ve öncelikli inceleme adımlarını çıkar. Her bulguda dosya ve satır aralığı belirt. Eksik verileri açıkça yaz.', 'web_enabled': False}},
    {'id': 'security-digest', 'name': 'Güvenlik bülteni', 'description': 'Tarih ve kaynak bağlantılarıyla gündem', 'builtin': True,
     'config': {'prompt': 'Güncel siber güvenlik gelişmelerini birincil kaynaklardan araştır. Yayın ve olay tarihlerini ayır; etkilenen ürünleri, uygulanabilir önlemleri, kaynak URL’lerini ve doğrulanmamış noktaları raporla.', 'web_enabled': True}},
    {'id': 'vulnerability-review', 'name': 'Zafiyet raporunu değerlendir', 'description': 'Kanıt, etki, alternatif açıklama ve giderim', 'builtin': True,
     'config': {'prompt': 'Yüklenen zafiyet raporunun kanıtlarını ve varsayımlarını değerlendir. Etki, sömürü önkoşulları, yanlış pozitif ihtimali, giderim ve doğrulama adımlarını kaynaklarıyla yaz.', 'review_enabled': True, 'mode': 'council'}},
]


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,instruction TEXT NOT NULL,owner_id TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,text TEXT NOT NULL,owner_id TEXT NOT NULL,project_id TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS task_files(task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,file_id TEXT REFERENCES files(id),PRIMARY KEY(task_id,file_id));
    CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,config TEXT NOT NULL,owner_id TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY,name TEXT NOT NULL,config TEXT NOT NULL,interval_minutes INTEGER NOT NULL,next_run REAL NOT NULL,enabled INTEGER NOT NULL,owner_id TEXT NOT NULL,last_task TEXT,last_error TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS integrations(id TEXT PRIMARY KEY,config TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,status TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL,sent_at TEXT);
    CREATE INDEX IF NOT EXISTS files_project ON files(project_id,created_at);
    ''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(tasks)')}
    for column in ('project_id TEXT', 'review_enabled INTEGER NOT NULL DEFAULT 0', "language TEXT NOT NULL DEFAULT 'tr'", 'notified INTEGER NOT NULL DEFAULT 0'):
        if column.split()[0] not in columns:
            db.execute('ALTER TABLE tasks ADD COLUMN ' + column)
    # A process may have stopped after a send; never automatically repeat an ambiguous delivery.
    db.execute("UPDATE notifications SET status='unknown',error='Sunucu gönderim sırasında yeniden başladı; otomatik tekrar yapılmadı' WHERE status='sending'")


def writable(user, owner):
    if user['role'] == 'viewer' or (user['role'] != 'admin' and user['id'] != owner):
        raise PermissionError('Bu kaydı yalnızca sahibi veya yönetici değiştirebilir')


def check_project(db, user, pid, write=False):
    access.check(db,user,pid,write)


def task_options(db, user, body, server):
    pid = body.get('project_id') or None
    check_project(db, user, pid, write=True)
    ids = body.get('file_ids', [])
    if not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(fid, str) for fid in ids) or len(set(ids)) != len(ids):
        raise ValueError('En fazla 20 farklı dosya seçebilirsin')
    rows = []
    for fid in ids:
        row = db.execute('SELECT id,project_id,kind FROM files WHERE id=?', (fid,)).fetchone()
        if not row or row['project_id'] != pid:
            raise ValueError('Dosyalar seçilen proje alanından olmalı')
        rows.append(row)
    for aid in body.get('agents', []) + ([body['fallback_agent']] if body.get('fallback_agent') else []):
        caps=server.BY_ID[aid].get('capabilities', {})
        if any(r['kind']=='image' for r in rows) and caps.get('vision') is False:
            raise ValueError('Seçilen ajan görsel desteklemiyor')
        if body.get('web_enabled') and caps.get('tools') is False:
            raise ValueError('Seçilen ajan araç çağrısı desteklemiyor')
    if any(r['kind'] == 'image' for r in rows):
        participants = body.get('agents', []) + ([body['fallback_agent']] if body.get('fallback_agent') else [])
        if any(server.BY_ID[x]['provider'] != 'openai-compatible' for x in participants):
            raise ValueError('Görseller için OpenAI uyumlu, görsel destekleyen model seç. Hermes bridge görsel sözleşmesi uygulamıyor')
    return pid, ids, int(body.get('review_enabled') is True and body.get('mode') == 'council')


def attach(db, tid, options):
    pid, fids, review = options
    db.execute('UPDATE tasks SET project_id=?,review_enabled=? WHERE id=?', (pid, review, tid))
    db.executemany('INSERT INTO task_files VALUES(?,?)', [(tid, fid) for fid in fids])


def clean_config(config, server):
    if not isinstance(config, dict):
        raise ValueError('Görev ayarları geçersiz')
    result = {k: config[k] for k in ('prompt', 'mode', 'agents', 'judge', 'fallback_agent', 'web_enabled', 'review_enabled', 'tool_requested', 'project_id', 'file_ids', 'knowledge_ids', 'mcp_servers', 'language', 'mask_sensitive', 'allow_unmasked_images') if k in config}
    if result.get('language', 'tr') not in ('tr', 'en'):
        raise ValueError('Dil tr veya en olmalı')
    prompt = result.get('prompt', '')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > server.MAX_PROMPT_CHARS:
        raise ValueError('Şablon bağlamı 1–16.000 karakter olmalı')
    ids = result.get('agents', [])
    if not isinstance(ids, list) or not 1 <= len(ids) <= 4 or any(not isinstance(a, str) or a not in server.BY_ID for a in ids) or len(set(ids)) != len(ids):
        raise ValueError('Şablon için 1–4 geçerli ajan seç')
    if result.get('mode', 'single') not in ('single', 'council') or result.get('mode', 'single') == 'single' and len(ids) != 1:
        raise ValueError('Çalışma biçimiyle ajan sayısı uyuşmuyor')
    for key in ('judge', 'fallback_agent'):
        if result.get(key) and result[key] not in server.BY_ID:
            raise ValueError('Baş uzman veya yedek ajan bulunamadı')
    return result


def integration_public(db):
    row = db.execute("SELECT config FROM integrations WHERE id='telegram'").fetchone()
    cfg = json.loads(row[0]) if row else {}
    return {'enabled': cfg.get('enabled', False), 'has_token': bool(cfg.get('token')), 'chat_id': cfg.get('chat_id', ''), 'origin': cfg.get('origin', ''), 'include_summary': cfg.get('include_summary', False)}


def state(db, user):
    projects=[]
    for row in db.execute('SELECT * FROM projects ORDER BY created_at'):
        grant=access.level(db,user,row['id'])
        if grant:projects.append(dict(row)|{'visibility':access.mode(db,row['id']),'access':grant,'can_manage':user['role']=='admin' or user['role']!='viewer' and row['owner_id']==user['id']})
    clause,params=access.sql(user,'files.project_id')
    records={}
    for table in ('templates','schedules'):
        records[table]=[dict(r)|{'config':json.loads(r['config'])} for r in db.execute('SELECT * FROM '+table+' ORDER BY created_at DESC') if access.level(db,user,json.loads(r['config']).get('project_id'))]
    return {'projects':projects,'files':[files.metadata(r) for r in db.execute('SELECT * FROM files WHERE '+clause+' ORDER BY created_at DESC',params)],
            'templates':BUILTINS+[r|{'builtin':False} for r in records['templates']],'schedules':records['schedules'],
            'telegram':integration_public(db) if user['role']=='admin' else {},
            'notifications':[dict(r) for r in db.execute('SELECT * FROM notifications ORDER BY created_at DESC LIMIT 50')] if user['role']=='admin' else []}


def handle_get(handler, user, path, db, server):
    if path == '/api/workspace':
        handler.respond(200, state(db, user)); return True
    if path == '/api/files/search':
        query = parse_qs(urlparse(handler.path).query)
        pid = query.get('project_id', ['__general'])[0]
        pid = None if pid == '__general' else pid
        check_project(db, user, pid)
        handler.respond(200, files.search(db, pid, query.get('q', [''])[0],
                                        int(query.get('offset', ['0'])[0]), int(query.get('limit', ['50'])[0])))
        return True
    if path.startswith('/api/files/'):

        fid = path.split('/')[-1]
        row = db.execute('SELECT * FROM files WHERE id=?', (fid,)).fetchone()
        if not row:
            handler.respond(404, {'error': 'Dosya bulunamadı'}); return True
        query = parse_qs(urlparse(handler.path).query)
        if query.get('image') == ['1'] and row['kind'] == 'image':
            content = (files.folder(server) / fid).read_bytes()
            handler.send_response(200); handler.headers_common(row['mime'])
            handler.send_header('Cache-Control', 'no-store'); handler.send_header('Content-Length', str(len(content))); handler.end_headers(); handler.wfile.write(content); return True
        start = max(1, int(query.get('start', ['1'])[0]))
        count = min(1000, max(1, int(query.get('count', ['200'])[0])))
        lines = row['text'].splitlines()
        handler.respond(200, {'file': files.metadata(row), 'start': start, 'total_lines': len(lines), 'lines': lines[start - 1:start - 1 + count]}); return True
    return False


def handle_post(handler, user, path, body, server):
    if path not in ('/api/projects', '/api/projects/delete', '/api/templates', '/api/templates/delete', '/api/files', '/api/files/delete', '/api/schedules', '/api/schedules/toggle', '/api/schedules/delete', '/api/telegram', '/api/notifications/retry'):
        return False
    if user['role'] == 'viewer':
        raise PermissionError('Gözlemci bu ayarı değiştiremez')
    ident = uuid.uuid4().hex
    with server.connect() as db:
        if path == '/api/projects':
            name, instruction = body.get('name', ''), body.get('instruction', '')
            if not isinstance(name, str) or not name.strip() or len(name) > 100 or not isinstance(instruction, str) or len(instruction) > 16000:
                raise ValueError('Proje adı 1–100, proje talimatı en fazla 16.000 karakter olmalı')
            existing = db.execute('SELECT * FROM projects WHERE id=?', (body.get('id'),)).fetchone()
            if body.get('id') and not existing:
                raise ValueError('Proje bulunamadı')
            visibility=body.get('visibility',access.mode(db,existing['id']) if existing else 'private')
            if visibility not in ('workspace','private'):raise ValueError('Proje görünürlüğü geçersiz')
            if existing:
                writable(user, existing['owner_id']); ident = existing['id']
                db.execute('UPDATE projects SET name=?,instruction=? WHERE id=?', (name.strip(), instruction, ident))
            else:
                db.execute('INSERT INTO projects VALUES(?,?,?,?,?)', (ident, name.strip(), instruction, user['id'], server.now()))
            db.execute('INSERT OR REPLACE INTO project_access VALUES(?,?)',(ident,visibility))
            access.cancel_disallowed(server,db,ident,user['id'])
        elif path == '/api/projects/delete':
            row = db.execute('SELECT * FROM projects WHERE id=?', (body.get('id'),)).fetchone()
            if not row:
                raise ValueError('Proje bulunamadı')
            writable(user, row['owner_id'])
            if db.execute('SELECT 1 FROM tasks WHERE project_id=?', (row['id'],)).fetchone() or db.execute('SELECT 1 FROM files WHERE project_id=?', (row['id'],)).fetchone():
                raise ValueError('Projenin dosyalarını ve görev geçmişini önce temizle')
            if any(json.loads(r[0]).get('project_id') == row['id'] for r in db.execute('SELECT config FROM schedules UNION ALL SELECT config FROM templates')):
                raise ValueError('Proje bir şablonda veya zamanlamada kullanılıyor')
            if db.execute('SELECT 1 FROM playbooks WHERE project_id=? UNION ALL SELECT 1 FROM playbook_runs WHERE project_id=?',(row['id'],row['id'])).fetchone():raise ValueError('Proje reçetelerde kullanılıyor; önce reçeteleri ve çalışmalarını temizle')
            if db.execute('SELECT 1 FROM lab_trials WHERE project_id=?',(row['id'],)).fetchone():raise ValueError('Proje model laboratuvarında kullanılıyor')
            db.execute('DELETE FROM projects WHERE id=?', (row['id'],))
        elif path == '/api/files':
            ident = files.upload(server, user, body)
        elif path == '/api/files/delete':
            row = db.execute('SELECT * FROM files WHERE id=?', (body.get('id'),)).fetchone()
            if not row:
                raise ValueError('Dosya bulunamadı')
            writable(user, row['owner_id'])
            if db.execute('SELECT 1 FROM task_files WHERE file_id=?', (row['id'],)).fetchone():
                raise ValueError('Dosya görev kanıtı olarak kullanılıyor; önce ilgili görev geçmişini temizle')
            if any(row['id'] in json.loads(r[0]).get('file_ids', []) for r in db.execute('SELECT config FROM schedules UNION ALL SELECT config FROM templates')):
                raise ValueError('Dosya bir şablonda veya zamanlamada kullanılıyor')
            db.execute('DELETE FROM files WHERE id=?', (row['id'],))
            (files.folder(server) / row['id']).unlink(missing_ok=True)
        elif path == '/api/templates':
            cfg = clean_config(body.get('config'), server)
            task_options(db, user, cfg, server)
            name = body.get('name', '')
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError('Şablon adı 1–100 karakter olmalı')
            db.execute('INSERT INTO templates VALUES(?,?,?,?,?,?)', (ident, name.strip(), '', json.dumps(cfg), user['id'], server.now()))
        elif path == '/api/templates/delete':
            row = db.execute('SELECT * FROM templates WHERE id=?', (body.get('id'),)).fetchone()
            if not row:
                raise ValueError('Şablon bulunamadı')
            writable(user, row['owner_id']); db.execute('DELETE FROM templates WHERE id=?', (row['id'],))
        elif path == '/api/schedules':
            cfg = clean_config(body.get('config'), server)
            task_options(db, user, cfg, server)
            minutes = body.get('interval_minutes')
            name = body.get('name', '')
            next_run = body.get('next_run', time.time() + (minutes * 60 if type(minutes) is int else 0))
            if type(minutes) is not int or not 5 <= minutes <= 525600 or not isinstance(name, str) or not name.strip() or len(name) > 100 or type(next_run) not in (int, float) or not time.time() - 60 <= next_run <= time.time() + 366 * 86400:
                raise ValueError('Zamanlama adı, ilk tarih ve 5–525600 dakika aralığını kontrol et')
            db.execute('INSERT INTO schedules VALUES(?,?,?,?,?,?,?,?,?,?)', (ident, name.strip(), json.dumps(cfg), minutes, next_run, 1, user['id'], None, None, server.now()))
        elif path.startswith('/api/schedules/'):
            row = db.execute('SELECT * FROM schedules WHERE id=?', (body.get('id'),)).fetchone()
            if not row:
                raise ValueError('Zamanlama bulunamadı')
            writable(user, row['owner_id'])
            if path.endswith('/delete'):
                db.execute('DELETE FROM schedules WHERE id=?', (row['id'],))
            else:
                db.execute('UPDATE schedules SET enabled=?,next_run=max(next_run,?) WHERE id=?', (int(body.get('enabled') is True), time.time(), row['id']))
        elif path == '/api/telegram':
            if user['role'] != 'admin':
                raise PermissionError('Telegram ayarı yönetici içindir')
            row = db.execute("SELECT config FROM integrations WHERE id='telegram'").fetchone()
            old = json.loads(row[0]) if row else {}
            token = body.get('token', '')
            chat = body.get('chat_id', '')
            origin = body.get('origin', '').rstrip('/')
            if not isinstance(token, str) or (token and not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token)) or len(token) > 500 or not isinstance(chat, str) or len(chat) > 100 or not isinstance(origin, str) or len(origin) > 2000:
                raise ValueError('Telegram ayarları geçersiz')
            if origin and (urlparse(origin).scheme not in ('https', 'http') or not urlparse(origin).hostname or urlparse(origin).username or urlparse(origin).password or urlparse(origin).query or urlparse(origin).fragment):
                raise ValueError('Rapor bağlantısı için güvenilir http(s) uygulama adresi kullan')
            encrypted = '' if body.get('clear_token') else server.encrypt(server.DB_PATH, token) if token else old.get('token', '')
            enabled = body.get('enabled') is True
            if enabled and (not encrypted or not chat.strip()):
                raise ValueError('Telegram için bot anahtarı ve sohbet kimliği gerekli')
            cfg = {'enabled': enabled, 'token': encrypted, 'chat_id': chat.strip(), 'origin': origin, 'include_summary': body.get('include_summary') is True, 'enabled_since': old.get('enabled_since', server.now()) if old.get('enabled') else server.now()}
            db.execute("INSERT OR REPLACE INTO integrations VALUES('telegram',?)", (json.dumps(cfg),))
        elif path == '/api/notifications/retry':
            if user['role'] != 'admin':
                raise PermissionError('Teslimatı yönetici yeniden deneyebilir')
            changed = db.execute("UPDATE notifications SET status='pending',error=NULL WHERE id=? AND status IN ('failed','unknown')", (body.get('id'),)).rowcount
            if not changed:
                raise ValueError('Yeniden denenebilir teslimat yok')
        server.log(db, None, 'workspace_updated', path.rsplit('/', 1)[-1] + ' · ' + user['id'],audit_target=body.get('id') or ident)
    handler.respond(201, {'id': ident, 'ok': True}); return True


def task_list(db, url, user=None):
    query=parse_qs(urlparse(url).query)
    clause,params=access.sql(user or {'id':'admin','role':'admin'},'tasks.project_id')
    if 'project_id' in query:
        pid=query['project_id'][0];pid=None if pid=='__general' else pid
        if user:check_project(db,user,pid)
        clause+=' AND project_id IS :selected_project';params['selected_project']=pid
    return [dict(r) for r in db.execute('SELECT * FROM tasks WHERE '+clause+' ORDER BY created_at DESC LIMIT 100',params)]
