"""Project investigation, conversation, knowledge and narrowly scoped inbound jobs."""
import difflib
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from urllib.parse import parse_qs, urlparse

TERMINAL = ('completed', 'incomplete', 'failed', 'cancelled')
STATUSES = ('new', 'investigating', 'confirmed', 'resolved', 'dismissed')
SEVERITIES = ('info', 'low', 'medium', 'high', 'critical')


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS findings(id TEXT PRIMARY KEY,project_id TEXT,owner_id TEXT NOT NULL,title TEXT NOT NULL,details TEXT NOT NULL,severity TEXT NOT NULL,status TEXT NOT NULL,task_id TEXT,file_id TEXT,first_line INTEGER,last_line INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS finding_events(id INTEGER PRIMARY KEY, finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,actor TEXT NOT NULL,snapshot TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS knowledge(id TEXT PRIMARY KEY,project_id TEXT,owner_id TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,source_task TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS webhooks(id TEXT PRIMARY KEY,name TEXT NOT NULL,owner_id TEXT NOT NULL,token_hash TEXT NOT NULL,config TEXT NOT NULL,enabled INTEGER NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS webhook_requests(hook_id TEXT NOT NULL REFERENCES webhooks(id),request_key TEXT NOT NULL,task_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(hook_id,request_key));
    CREATE INDEX IF NOT EXISTS findings_project ON findings(project_id,updated_at);
    CREATE INDEX IF NOT EXISTS knowledge_project ON knowledge(project_id,created_at);
    ''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(tasks)')}
    for definition in ('parent_task TEXT', 'root_task TEXT'):
        if definition.split()[0] not in columns:
            db.execute('ALTER TABLE tasks ADD COLUMN '+definition)
    for definition in ('model_snapshot TEXT', 'provider_snapshot TEXT', 'agent_name_snapshot TEXT'):
        if definition.split()[0] not in {r[1] for r in db.execute('PRAGMA table_info(runs)')}:
            db.execute('ALTER TABLE runs ADD COLUMN '+definition)
    if 'finished_at' not in {r[1] for r in db.execute('PRAGMA table_info(runs)')}:
        db.execute('ALTER TABLE runs ADD COLUMN finished_at TEXT')


def text(body, key, maximum, required=True):
    value = body.get(key, '')
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError('Metin alanını kontrol et: '+key)
    return value.strip()


def report(db, tid):
    task = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    if not task:
        raise ValueError('Görev bulunamadı')
    outputs = [dict(r) for r in db.execute('SELECT id,agent_id,kind,output,status,input_tokens,output_tokens,cost,created_at,finished_at,model_snapshot,provider_snapshot,agent_name_snapshot FROM runs WHERE task_id=? ORDER BY created_at,id', (tid,))]
    content = task['synthesis'] or '\n\n'.join((r['output'] or '') for r in outputs if r['kind']=='participant')
    if not content:
        content = '\n\n'.join(r['output'] or '' for r in outputs)
    return task, outputs, content


def thread(db, tid, server):
    task = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    if not task:
        raise ValueError('Görev bulunamadı')
    root = task['root_task'] or tid
    rows = db.execute('SELECT * FROM tasks WHERE id=? OR root_task=? ORDER BY created_at,id', (root, root))
    result = []
    for row in rows:
        _, runs, content = report(db, row['id'])
        result.append({'task': server.task_dict(row), 'content': content})
    return {'root': root, 'turns': result}


def followup(server, user, tid, body):
    with server.CONFIG_LOCK, server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        if not old or old['status'] not in TERMINAL:
            raise ValueError('Önceki görev bitmiş olmalı')
        server.workspace.writable(user, old['owner_id'])
        cfg = server.task_dict(old)
        cfg.pop('context_preview_id',None)
        cfg.update(prompt=body.get('prompt', ''), language=body.get('language', old['language']),mcp_servers=[s['id'] for s in json.loads(old['mcp_selection'])])
        original = [r[0] for r in db.execute('SELECT file_id FROM task_files WHERE task_id=?', (tid,))]
        added = body.get('file_ids', [])
        if not isinstance(added, list) or any(not isinstance(x, str) for x in added):
            raise ValueError('Dosya listesi geçersiz')
        cfg['file_ids'] = list(dict.fromkeys(original+added))
        prior = old['continuation_context'] + '\n\nUSER:\n' + old['prompt']
        for row in db.execute('SELECT agent_id,kind,output FROM runs WHERE task_id=? ORDER BY created_at,id', (tid,)):
            if row['output']:
                prior += '\n\nASSISTANT ('+row['agent_id']+'/'+row['kind']+'):\n'+row['output']
        if old['synthesis']:
            prior += '\n\nSYNTHESIS:\n'+old['synthesis']
        ident = server.create_task(user, cfg, continuation_context=prior, db=db)
        db.execute('UPDATE tasks SET parent_task=?,root_task=? WHERE id=?', (tid, old['root_task'] or tid, ident))
        return ident


def knowledge_context(db, pid, ids):
    if not isinstance(ids, list) or len(ids)>20 or any(not isinstance(x, str) for x in ids) or len(set(ids))!=len(ids):
        raise ValueError('Bilgi seçimi geçersiz')
    parts=[]
    for ident in ids:
        row=db.execute('SELECT * FROM knowledge WHERE id=? AND project_id IS ?', (ident,pid)).fetchone()
        if not row:
            raise ValueError('Bilgi seçilen proje alanından olmalı')
        parts.append('UNTRUSTED KNOWLEDGE [knowledge:'+ident+'] '+row['title']+'\n'+row['content'])
    return '\n\n'.join(parts)


def capabilities(body, previous):
    value=body.get('capabilities', previous.get('capabilities', {}))
    if not isinstance(value, dict):
        raise ValueError('Yetenek bilgileri geçersiz')
    result={}
    for key in ('vision','tools'):
        val=value.get(key, None)
        if val is not None and type(val) is not bool:
            raise ValueError('Yetenek bilgileri geçersiz')
        result[key]=val
    context=value.get('context_tokens')
    if context is not None and (type(context) is not int or not 1<=context<=100000000):
        raise ValueError('Bağlam kapasitesi geçersiz')
    result['context_tokens']=context
    tags=value.get('specialties', [])
    if not isinstance(tags,list) or len(tags)>12 or any(not isinstance(t,str) or not t.strip() or len(t)>60 for t in tags):
        raise ValueError('Uzmanlık etiketleri geçersiz')
    result['specialties']=list(dict.fromkeys(t.strip() for t in tags))
    return result


def receive_webhook(server, ident, token, key, body):
    if not isinstance(key,str) or not 1<=len(key)<=200:
        raise ValueError('Idempotency-Key gerekli (1–200 karakter)')
    with server.CONFIG_LOCK, server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        hook=db.execute('SELECT * FROM webhooks WHERE id=? AND enabled=1',(ident,)).fetchone()
        if not hook or not hmac.compare_digest(hook['token_hash'],hashlib.sha256(token.encode()).hexdigest()):
            raise PermissionError('Webhook yetkisi geçersiz')
        existing=db.execute('SELECT task_id FROM webhook_requests WHERE hook_id=? AND request_key=?',(ident,key)).fetchone()
        if existing:
            return {'id':existing[0],'replayed':True}
        user=db.execute('SELECT * FROM users WHERE id=?',(hook['owner_id'],)).fetchone()
        if not user or user['role']!='admin':
            raise PermissionError('Webhook sahibi yönetici olmalı')
        cfg=json.loads(hook['config'])
        cfg['prompt']=text(body,'prompt',server.MAX_PROMPT_CHARS)
        # Configuration, providers, project and tools cannot be overridden by callers.
        cfg['tool_requested']=False
        task=server.create_task(dict(user),cfg,db=db)
        db.execute('INSERT INTO webhook_requests VALUES(?,?,?,?)',(ident,key,task,server.now()))
        server.log(db,task,'webhook_received','Webhook · '+ident)
        return {'id':task,'replayed':False}


def handle_get(handler,user,path,db,server):
    if not path.startswith('/api/platform/'):
        return False
    if path=='/api/platform/backup/status':
        if user['role']!='admin':raise PermissionError('Yönetici erişimi gerekli')
        from .backup import readiness
        handler.respond(200,readiness(db));return True
    query=parse_qs(urlparse(handler.path).query)
    pid=query.get('project_id',['__general'])[0]
    pid=None if pid=='__general' else pid
    if path=='/api/platform/thread':
        handler.respond(200,thread(db,query.get('task',[''])[0],server));return True
    if path=='/api/platform/compare':
        ids=query.get('ids',[''])[0].split(',')
        if not 2<=len(ids)<=4 or len(set(ids))!=len(ids):
            raise ValueError('Karşılaştırmak için 2–4 farklı görev seç')
        items=[]
        for ident in ids:
            task,runs,content=report(db,ident)
            items.append({'task':server.task_dict(task),'runs':runs,'content':content,
                          'input_tokens':sum(r['input_tokens'] or 0 for r in runs),'output_tokens':sum(r['output_tokens'] or 0 for r in runs)})
        diffs=[{'left':ids[0],'right':item['task']['id'],'text':'\n'.join(difflib.unified_diff(items[0]['content'].splitlines(),item['content'].splitlines(),fromfile=ids[0],tofile=item['task']['id'],lineterm=''))} for item in items[1:]]
        handler.respond(200,{'items':items,'diffs':diffs});return True
    if path=='/api/platform/activity':
        clause,params=server.access.sql(user,'t.project_id')
        tasks=[server.task_dict(r) for r in db.execute("SELECT t.* FROM tasks t WHERE t.status IN ('queued','running','awaiting_approval') AND "+clause+" ORDER BY t.created_at",params)]
        runs=[dict(r) for r in db.execute("SELECT r.id,r.task_id,r.agent_id,r.kind,r.status,r.created_at,r.finished_at,r.input_tokens,r.output_tokens FROM runs r JOIN tasks t ON t.id=r.task_id WHERE t.status IN ('queued','running','awaiting_approval') AND "+clause+" ORDER BY r.created_at",params)]
        events=[dict(r) for r in db.execute('SELECT l.id,l.task_id,l.event,l.message,l.created_at FROM logs l LEFT JOIN tasks t ON t.id=l.task_id WHERE '+clause+("" if user['role']=='admin' else ' AND t.id IS NOT NULL')+' ORDER BY l.id DESC LIMIT 100',params)]
        handler.respond(200,{'tasks':tasks,'runs':runs,'events':events,'server_time':server.now()});return True
    if path=='/api/platform/webhooks':
        if user['role']!='admin':raise PermissionError('Yönetici erişimi gerekli')
        handler.respond(200,{'items':[dict(r) | {'config':json.loads(r['config'])} for r in db.execute('SELECT id,name,owner_id,config,enabled,created_at FROM webhooks ORDER BY created_at DESC')]});return True
    server.workspace.check_project(db,user,pid)
    if path=='/api/platform/findings':
        rows=[dict(r) for r in db.execute('SELECT * FROM findings WHERE project_id IS ? ORDER BY updated_at DESC',(pid,))]
        for row in rows:
            row['history']=[dict(r) | {'snapshot':json.loads(r['snapshot'])} for r in db.execute('SELECT * FROM finding_events WHERE finding_id=? ORDER BY id',(row['id'],))]
        handler.respond(200,{'items':rows});return True
    if path=='/api/platform/knowledge':
        needle=query.get('q',[''])[0].strip()
        if len(needle)>200:raise ValueError('Arama metni çok uzun')
        pattern=re.compile(re.escape(needle),re.I) if needle else None
        notes=[dict(r) for r in db.execute('SELECT * FROM knowledge WHERE project_id IS ? ORDER BY created_at DESC',(pid,))]
        reports=[]
        if pattern:
            for row in db.execute('SELECT id,title FROM tasks WHERE project_id IS ? ORDER BY created_at DESC',(pid,)):
                task,runs,content=report(db,row['id'])
                match=pattern.search(content)
                if match or pattern.search(row['title']):
                    start=max(0,(match.start() if match else 0)-120)
                    reports.append({'id':row['id'],'title':row['title'],'snippet':content[start:start+500]})
        matches=server.files.search(db,pid,needle) if needle else {'results':[],'next_offset':None}
        handler.respond(200,{'items':[r for r in notes if not pattern or pattern.search(r['title']+'\n'+r['content'])],'reports':reports,'file_matches':matches['results'],'files_next':matches['next_offset']});return True
    return False


def handle_post(handler,user,path,body,server):
    if not path.startswith('/api/platform/'):
        return False
    if user['role']=='viewer':raise PermissionError('Gözlemci bu ayarı değiştiremez')
    if path=='/api/platform/backup':
        if user['role']!='admin':raise PermissionError('Yönetici erişimi gerekli')
        from .backup import create
        content=create(server,body.get('password',''))
        handler.send_response(200);handler.headers_common('application/octet-stream')
        handler.send_header('Cache-Control','no-store');handler.send_header('Content-Disposition','attachment; filename="ecy-harbor.ecybak"')
        handler.send_header('Content-Length',str(len(content)));handler.end_headers();handler.wfile.write(content);return True
    ident=body.get('id') or uuid.uuid4().hex
    if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{32}',ident):raise ValueError('Geçersiz kayıt kimliği')
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        pid=body.get('project_id') or None
        server.workspace.check_project(db,user,pid,write=True)
        if path=='/api/platform/findings':
            old=db.execute('SELECT * FROM findings WHERE id=?',(ident,)).fetchone()
            if old:
                server.workspace.writable(user,old['owner_id'])
                if pid!=old['project_id']:raise ValueError('Bulgunun projesi değiştirilemez')
            title=text(body,'title',200);details=text(body,'details',32000,False)
            status=body.get('status','new');severity=body.get('severity','medium')
            if status not in STATUSES or severity not in SEVERITIES:raise ValueError('Bulgu durumu geçersiz')
            task_id=body.get('task_id') or None;file_id=body.get('file_id') or None
            if task_id and not db.execute('SELECT 1 FROM tasks WHERE id=? AND project_id IS ?',(task_id,pid)).fetchone():raise ValueError('Kaynak görev seçilen projeden olmalı')
            first=body.get('first_line');last=body.get('last_line')
            if file_id:
                row=db.execute('SELECT * FROM files WHERE id=? AND project_id IS ?',(file_id,pid)).fetchone()
                if not row or row['kind']!='text' or type(first) is not int or type(last) is not int or not 1<=first<=last<=len(row['text'].splitlines()):raise ValueError('Kaynak satır aralığı geçersiz')
            elif first is not None or last is not None:raise ValueError('Kaynak dosya gerekli')
            values=(title,details,severity,status,task_id,file_id,first,last,server.now())
            if old:db.execute('UPDATE findings SET title=?,details=?,severity=?,status=?,task_id=?,file_id=?,first_line=?,last_line=?,updated_at=? WHERE id=?',values+(ident,))
            else:db.execute('INSERT INTO findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,pid,user['id'],title,details,severity,status,task_id,file_id,first,last,server.now(),server.now()))
            db.execute('INSERT INTO finding_events(finding_id,actor,snapshot,created_at) VALUES(?,?,?,?)',(ident,user['id'],json.dumps(dict(db.execute('SELECT * FROM findings WHERE id=?',(ident,)).fetchone())),server.now()))
        elif path=='/api/platform/knowledge':
            title=text(body,'title',200)
            source=body.get('source_task') or None
            if source:
                task,_,content=report(db,source)
                if task['project_id']!=pid:raise ValueError('Kaynak görev seçilen projeden olmalı')
                if task['status'] not in TERMINAL or not content:raise ValueError('Kaynak rapor hazır değil')
            else:content=text(body,'content',64000)
            db.execute('INSERT INTO knowledge VALUES(?,?,?,?,?,?,?)',(ident,pid,user['id'],title,content,source,server.now()))
        elif path=='/api/platform/knowledge/delete':
            row=db.execute('SELECT * FROM knowledge WHERE id=?',(ident,)).fetchone()
            if not row:raise ValueError('Bilgi bulunamadı')
            server.workspace.writable(user,row['owner_id']);db.execute('DELETE FROM knowledge WHERE id=?',(ident,))
        elif path=='/api/platform/webhooks':
            if user['role']!='admin':raise PermissionError('Yönetici erişimi gerekli')
            cfg=server.workspace.clean_config(body.get('config'),server)
            server.workspace.task_options(db,user,cfg,server)
            cfg['tool_requested']=False
            knowledge_context(db,cfg.get('project_id') or None,cfg.get('knowledge_ids',[]))
            name=text(body,'name',100);token=secrets.token_urlsafe(40)
            db.execute('INSERT INTO webhooks VALUES(?,?,?,?,?,?,?)',(ident,name,user['id'],hashlib.sha256(token.encode()).hexdigest(),json.dumps(cfg),1,server.now()))
            server.log(db,None,'webhook_created',ident)
            # Token is deliberately returned once and never stored in recoverable plaintext.
            handler.respond(201,{'id':ident,'token':token});return True
        elif path=='/api/platform/webhooks/revoke':
            if user['role']!='admin':raise PermissionError('Yönetici erişimi gerekli')
            if not db.execute('UPDATE webhooks SET enabled=0 WHERE id=?',(ident,)).rowcount:raise ValueError('Webhook bulunamadı')
        else:return False
        server.log(db,None,'platform_updated',path.rsplit('/',1)[-1]+' · '+user['id'])
    handler.respond(201,{'id':ident,'ok':True});return True
