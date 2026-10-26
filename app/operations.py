"""Scoped heartbeat telemetry and durable provider circuit breakers, never remote commands."""
import hashlib
import hmac
import json
import math
import secrets
import time
from urllib.error import HTTPError,URLError

class CircuitOpen(RuntimeError):pass
class DispatchPaused(RuntimeError):pass


def dispatch_paused(db):
    return bool(db.execute('SELECT paused FROM dispatch_control WHERE id=1').fetchone()[0])



def initialize_dispatch(db):
    db.execute("CREATE TABLE IF NOT EXISTS dispatch_control(id INTEGER PRIMARY KEY CHECK(id=1),paused INTEGER NOT NULL CHECK(paused IN (0,1)),changed_by TEXT NOT NULL,changed_at TEXT NOT NULL)")
    db.execute("INSERT OR IGNORE INTO dispatch_control VALUES(1,0,'system','')")


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS provider_circuits(id TEXT PRIMARY KEY,failures INTEGER NOT NULL,open_until REAL NOT NULL,lease_until REAL NOT NULL,last_failure REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY,name TEXT NOT NULL,hardware TEXT NOT NULL,token_hash TEXT NOT NULL,enabled INTEGER NOT NULL,last_seen REAL,metrics TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);''')
    initialize_dispatch(db)
    db.execute('UPDATE provider_circuits SET lease_until=0,open_until=max(open_until,?) WHERE lease_until>0',(time.time()+60,))


def key(agent):return hashlib.sha256((agent.get('endpoint','')+'\n'+agent['model']).encode()).hexdigest()


def admit(server,agent):
    if server.MODE=='mock':return None
    ident=key(agent);now=time.time()
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM provider_circuits WHERE id=?',(ident,)).fetchone()
        if row and (row['open_until']>now or row['lease_until']>0):raise CircuitOpen('Sağlayıcı geçici dinlenmede; daha sonra yeniden dene')
        if row and row['failures']>=3:db.execute('UPDATE provider_circuits SET lease_until=? WHERE id=?',(now,ident))
    return ident


def success(server,ident):
    if ident:
        with server.connect() as db:db.execute('DELETE FROM provider_circuits WHERE id=?',(ident,))


def transient(exc):
    if isinstance(exc,HTTPError):return exc.code==429 or exc.code>=500
    return isinstance(exc,(URLError,TimeoutError,ConnectionError,OSError))


def failure(server,ident):
    if not ident:return
    now=time.time()
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM provider_circuits WHERE id=?',(ident,)).fetchone()
        count=(row['failures']+1 if row and now-row['last_failure']<=300 else 1)
        if row and row['lease_until']>0:count=max(3,count)
        db.execute('INSERT OR REPLACE INTO provider_circuits VALUES(?,?,?,?,?)',(ident,count,now+60 if count>=3 else 0,0,now))


def release(server,ident):
    if ident:
        with server.connect() as db:db.execute('UPDATE provider_circuits SET lease_until=0,open_until=max(open_until,?) WHERE id=? AND lease_until>0',(time.time()+60,ident))


def authenticate(server,ident,header):
    if not isinstance(header,str) or not header.startswith('Bearer ') or len(header)>512:raise PermissionError('Düğüm anahtarı geçersiz')
    with server.connect() as db:row=db.execute('SELECT token_hash,enabled FROM nodes WHERE id=?',(ident,)).fetchone()
    expected=row['token_hash'] if row else '0'*64
    if not hmac.compare_digest(expected,hashlib.sha256(header[7:].encode()).hexdigest()) or not row or not row['enabled']:raise PermissionError('Düğüm anahtarı geçersiz')


def heartbeat(server,ident,header,body):
    authenticate(server,ident,header)
    if set(body)-{'status','running_jobs','load_1m','memory_used_mb','memory_total_mb'}:raise ValueError('Düğüm ölçüm alanları geçersiz')
    status=body.get('status','online');jobs=body.get('running_jobs',0)
    if status not in ('online','busy','degraded') or type(jobs) is not int or not 0<=jobs<=10000:raise ValueError('Düğüm durumu geçersiz')
    for k in ('load_1m','memory_used_mb','memory_total_mb'):
        if k in body and (type(body[k]) not in (int,float) or not math.isfinite(body[k]) or not 0<=body[k]<=10**9):raise ValueError('Düğüm ölçümü geçersiz')
    if body.get('memory_used_mb',0)>body.get('memory_total_mb',10**9):raise ValueError('Düğüm bellek ölçümü geçersiz')
    with server.connect() as db:
        # Recheck revocation after validation; credentials only write this node's telemetry.
        if not db.execute('UPDATE nodes SET last_seen=?,metrics=? WHERE id=? AND enabled=1 AND token_hash=?',(time.time(),json.dumps(dict(body,status=status,running_jobs=jobs)),ident,hashlib.sha256(header[7:].encode()).hexdigest())).rowcount:raise PermissionError('Düğüm anahtarı geçersiz')
    return {'ok':True}


def handle_get(handler,user,path,db,server):
    if path!='/api/operations':return False
    now=time.time();nodes=[]
    for row in db.execute('SELECT id,name,hardware,enabled,last_seen,metrics FROM nodes ORDER BY created_at'):
        n=dict(row);n['metrics']=json.loads(n['metrics']);n['state']='disabled' if not n['enabled'] else 'unknown' if n['last_seen'] is None else 'stale' if now-n['last_seen']>120 else n['metrics'].get('status','online');nodes.append(n)
    providers=[]
    for agent in server.AGENTS:
        row=db.execute('SELECT * FROM provider_circuits WHERE id=?',(key(agent),)).fetchone()
        providers.append({'agent_id':agent['id'],'failures':row['failures'] if row else 0,'state':'cooldown' if row and row['open_until']>now else 'probe' if row and row['failures']>=3 else 'closed','retry_at':row['open_until'] if row and row['open_until']>now else None})
    clause,params=server.access.sql(user,'tasks.project_id')
    counts={r['status']:r['n'] for r in db.execute('SELECT status,count(*) n FROM tasks WHERE '+clause+' GROUP BY status',params)}
    oldest=db.execute("SELECT min(created_at) FROM tasks WHERE status='queued' AND "+clause,params).fetchone()[0]
    control=dict(db.execute('SELECT paused,changed_by,changed_at FROM dispatch_control WHERE id=1').fetchone());control['paused']=bool(control['paused'])
    live_runs=db.execute("SELECT count(*) FROM runs WHERE status='running'").fetchone()[0]
    live_tasks=db.execute("SELECT count(*) FROM tasks WHERE status='running'").fetchone()[0]
    # Only administrators see global drain counts; other roles keep scoped queue metrics.
    control['running']=max(live_runs,live_tasks) if user['role']=='admin' else None
    control['drained']=control['paused'] and live_runs==0 and live_tasks==0 if user['role']=='admin' else None
    handler.respond(200,{'dispatch':control,'nodes':nodes,'providers':providers,'counts':counts,'oldest_queued_at':oldest,'server_time':now});return True


def handle_post(handler,user,path,body,server):
    if path not in ('/api/operations/nodes','/api/operations/nodes/toggle','/api/operations/nodes/delete','/api/operations/reset','/api/operations/dispatch'):return False
    if user['role']!='admin':raise PermissionError('Yönetici erişimi gerekli')
    with server.connect() as db:
        if path=='/api/operations/dispatch':
            db.execute('BEGIN IMMEDIATE')
            current=dispatch_paused(db)
            if type(body.get('paused')) is not bool or type(body.get('expected_paused')) is not bool:raise ValueError('Kuyruk durumu için gerçek boolean değerleri gerekli')
            if body['expected_paused']!=current:raise ValueError('Kuyruk durumu değişti; yenileyip tekrar dene')
            if body['paused']!=current:
                db.execute('UPDATE dispatch_control SET paused=?,changed_by=?,changed_at=? WHERE id=1',(int(body['paused']),user['id'],server.now()))
                server.log(db,None,'dispatch_paused' if body['paused'] else 'dispatch_resumed','Görev işleme durumu değişti')
            data={'ok':True,'paused':body['paused']}
        elif path=='/api/operations/nodes':
            name=server.platform.text(body,'name',100);hardware=server.platform.text(body,'hardware',200,False)
            if db.execute('SELECT count(*) FROM nodes').fetchone()[0]>=50:raise ValueError('En fazla 50 düğüm kaydedilebilir')
            ident=secrets.token_hex(16);token=secrets.token_urlsafe(32)
            db.execute('INSERT INTO nodes(id,name,hardware,token_hash,enabled,created_at) VALUES(?,?,?,?,1,?)',(ident,name,hardware,hashlib.sha256(token.encode()).hexdigest(),server.now()))
            data={'id':ident,'token':token,'endpoint':'/api/node-heartbeats/'+ident}
        elif path=='/api/operations/reset':
            aid=body.get('agent');agent=server.BY_ID.get(aid)
            if not agent:raise ValueError('Ajan bulunamadı')
            db.execute('DELETE FROM provider_circuits WHERE id=?',(key(agent),));data={'ok':True}
        elif path.endswith('/delete'):
            db.execute('DELETE FROM nodes WHERE id=?',(body.get('id'),));data={'ok':True}
        else:
            db.execute('UPDATE nodes SET enabled=? WHERE id=?',(int(body.get('enabled') is True),body.get('id')));data={'ok':True}
        if path!='/api/operations/dispatch':server.log(db,None,'operations_configured',path+' · '+user['id'])
    handler.respond(200,data);return True
