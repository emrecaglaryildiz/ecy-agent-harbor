"""Content-free administrative audit trail; local hash chaining is not host-root protection."""
import contextvars
import datetime as dt
import hashlib
import json
import re
import secrets
from urllib.parse import parse_qs,urlparse

CONTEXT=contextvars.ContextVar('harbor_audit_context',default=None)
EVENTS=frozenset(('access_updated','agent_saved','agent_deleted','agent_toggled','history_deleted','queued','agent_started','agent_completed','agent_incomplete','agent_failed','approval_required','tool_approved','tool_rejected','cancelled','recovery','workspace_updated','project_membership_changed','project_access_revoked','mfa_updated','mcp_configured','mcp_updated','mcp_approved','mcp_rejected','mcp_prepared','mcp_call_finished','operations_configured','platform_updated','webhook_created','webhook_received','playbook_created','playbook_run_created','playbook_step_started','playbook_control','lab_created','lab_rated','lab_action','finding_confidence','incident_action','login_succeeded','logout','backup_created','dispatch_paused','dispatch_resumed','backup_restored'))
FIELDS=('seq','stream_id','created_at','actor_id','initiator_id','event','request_id','route','target_id','task_id','previous_hash')
ZERO='0'*64
ID=re.compile(r'[a-f0-9]{32}\Z')


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def digest(value):return hashlib.sha256(canonical({k:value[k] for k in FIELDS}).encode()).hexdigest()
def safe_id(value):return value if isinstance(value,str) and ID.fullmatch(value) else ''
def safe_target(value):
    if safe_id(value):return value
    if isinstance(value,str) and re.fullmatch(r'(?:account:(?:admin|operator|viewer)|agent:(?:hermes|local|research|critic)|project:[a-f0-9]{32}/member:(?:admin|operator|viewer)/access:(?:read|write|none))',value):return value
    return ''
def safe_actor(value):return value if value in ('admin','operator','viewer','system','unknown','webhook') or safe_id(value) else 'unknown'


def route(path):
    parts=path.split('/')
    families={'projects','files','templates','schedules','telegram','notifications','access','agents','history','tasks','approvals','platform','mcp','operations','security','lab','playbooks','incidents','login','logout','setup','hooks','browser'}
    actions={'members','delete','toggle','password','retry','cancel','continue','followup','approve','reject','policy','discover','disable','call','nodes','reset','mfa','begin','confirm','backup','webhooks','revoke','knowledge','findings','confidence','actions','transition','runs','advance','pause','resume','start','rate','judge','install','dispatch'}
    if len(parts)<3 or parts[1]!='api' or parts[2] not in families:return 'other'
    return '/api/'+parts[2]+''.join('/'+(p if p in actions else ':id') for p in parts[3:6])


def begin(handler,user,body=None):
    body=body or {};path=urlparse(handler.path).path
    target=safe_id(body.get('id')) or safe_id(body.get('project_id'))
    if not target:
        target=next((safe_id(p) for p in path.split('/') if safe_id(p)),'')
    if path=='/api/access/password' or path.startswith('/api/security/mfa/'):
        account=body.get('user',user['id'])
        if account in ('admin','operator','viewer'):target='account:'+account
    if path.startswith('/api/agents/') and not target and len(path.split('/'))>3 and path.split('/')[3] in ('hermes','local','research','critic'):target='agent:'+path.split('/')[3]
    CONTEXT.set({'actor_id':safe_actor(user['id']),'request_id':handler.request_id,'route':route(path),'target_id':target})


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS audit_meta(id INTEGER PRIMARY KEY CHECK(id=1),stream_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit_events(seq INTEGER PRIMARY KEY,stream_id TEXT NOT NULL,created_at TEXT NOT NULL,actor_id TEXT NOT NULL,initiator_id TEXT NOT NULL,event TEXT NOT NULL,request_id TEXT NOT NULL,route TEXT NOT NULL,target_id TEXT NOT NULL,task_id TEXT NOT NULL,previous_hash TEXT NOT NULL,hash TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS audit_event_filter ON audit_events(event,seq);
    CREATE INDEX IF NOT EXISTS audit_actor_filter ON audit_events(actor_id,seq);
    CREATE INDEX IF NOT EXISTS audit_request_filter ON audit_events(request_id,seq);
    CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'Audit records are append only'); END;
    CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'Audit records are append only'); END;
    ''')
    db.execute('INSERT OR IGNORE INTO audit_meta VALUES(1,?)',(secrets.token_hex(16),))


def emit(db,event,task_id=None,target_id=None):
    if event not in EVENTS:return
    if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
    context=CONTEXT.get() or {};task_id=safe_id(task_id);initiator=''
    if task_id:
        owner=db.execute('SELECT owner_id FROM tasks WHERE id=?',(task_id,)).fetchone()
        if owner:initiator=safe_actor(owner[0])
    head=db.execute('SELECT seq,hash FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone()
    row={'seq':head[0]+1 if head else 1,'stream_id':db.execute('SELECT stream_id FROM audit_meta WHERE id=1').fetchone()[0],
         'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'actor_id':safe_actor(context.get('actor_id','system')),'initiator_id':initiator,
         'event':event,'request_id':safe_id(context.get('request_id')),'route':context.get('route','background'),'target_id':safe_target(target_id) or safe_target(context.get('target_id','')),'task_id':task_id,'previous_hash':head[1] if head else ZERO}
    row['hash']=digest(row)
    db.execute('INSERT INTO audit_events('+','.join(FIELDS)+',hash) VALUES('+','.join('?' for _ in range(len(FIELDS)+1))+')',tuple(row[k] for k in FIELDS)+(row['hash'],))


def checkpoint(db):
    row=db.execute('SELECT m.stream_id,e.seq,e.hash FROM audit_meta m LEFT JOIN audit_events e ON e.seq=(SELECT max(seq) FROM audit_events) WHERE m.id=1').fetchone()
    return {'format':1,'algorithm':'sha256-canonical-json-v1','stream_id':row[0],'seq':row[1] or 0,'hash':row[2] or ZERO}


def verify_chunk(db,after,until):
    if after<0 or until<after or until>checkpoint(db)['seq']:raise ValueError('Denetim aralığı geçersiz')
    previous=db.execute('SELECT hash FROM audit_events WHERE seq=?',(after,)).fetchone() if after else None
    if after and not previous:return {'valid':False,'checked':0,'next_after':None}
    expected=previous[0] if previous else ZERO;seq=after;rows=db.execute('SELECT * FROM audit_events WHERE seq>? AND seq<=? ORDER BY seq LIMIT 500',(after,until)).fetchall()
    stream=checkpoint(db)['stream_id']
    for raw in rows:
        row=dict(raw)
        if row['seq']!=seq+1 or row['stream_id']!=stream or row['previous_hash']!=expected or row['hash']!=digest(row):return {'valid':False,'checked':seq-after,'next_after':None}
        expected=row['hash'];seq=row['seq']
    valid=bool(rows) or after==until
    return {'valid':valid,'checked':seq-after,'next_after':seq if valid and seq<until else None}


def handle_get(handler,user,path,db,server):
    if path not in ('/api/audit','/api/audit/checkpoint','/api/audit/verify','/api/audit/export'):return False
    if user['role']!='admin':raise PermissionError('Denetim kayıtları yalnızca yönetici içindir')
    query=parse_qs(urlparse(handler.path).query)
    if path.endswith('/checkpoint'):handler.respond(200,checkpoint(db));return True
    if path.endswith('/verify'):
        after=int(query.get('after',['0'])[0]);until=int(query.get('until',[str(checkpoint(db)['seq'])])[0])
        handler.respond(200,verify_chunk(db,after,until));return True
    if path.endswith('/export'):
        head=checkpoint(db)
        handler.send_response(200);handler.headers_common('application/x-ndjson; charset=utf-8');handler.send_header('Cache-Control','no-store')
        handler.send_header('Content-Disposition','attachment; filename="harbor-audit.jsonl"');handler.end_headers();handler.close_connection=True
        handler.wfile.write((canonical({'type':'checkpoint',**head})+'\n').encode())
        after=0
        while after<head['seq']:
            rows=db.execute('SELECT * FROM audit_events WHERE seq>? AND seq<=? ORDER BY seq LIMIT 500',(after,head['seq'])).fetchall()
            if not rows:break # The offline verifier rejects an incomplete prefix.
            for row in rows:handler.wfile.write((canonical({'type':'event',**dict(row)})+'\n').encode())
            after=rows[-1]['seq']
        return True
    before=int(query.get('before',[str(checkpoint(db)['seq']+1)])[0]);limit=int(query.get('limit',['50'])[0]);event=query.get('event',[''])[0];actor=query.get('actor',[''])[0];request=query.get('request_id',[''])[0]
    if before<1 or not 1<=limit<=100 or event and event not in EVENTS or actor and safe_actor(actor)!=actor or request and not safe_id(request):raise ValueError('Denetim filtresi geçersiz')
    clauses=['seq<?'];params=[before]
    for key,value in (('event',event),('actor_id',actor),('request_id',request)):
        if value:clauses.append(key+'=?');params.append(value)
    rows=[dict(r) for r in db.execute('SELECT * FROM audit_events WHERE '+' AND '.join(clauses)+' ORDER BY seq DESC LIMIT ?',params+[limit+1])]
    more=len(rows)>limit;items=rows[:limit]
    handler.respond(200,{'items':items,'next_before':items[-1]['seq'] if more else None,'checkpoint':checkpoint(db),'events':sorted(EVENTS)});return True
