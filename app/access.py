"""Single-workspace project membership. Global administrators remain trusted operators."""
import json
from urllib.parse import parse_qs,urlparse

DENIED='Bu proje için erişim yetkin yok'


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS project_access(project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,mode TEXT NOT NULL CHECK(mode IN ('workspace','private')));
    CREATE TABLE IF NOT EXISTS project_members(project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id),access TEXT NOT NULL CHECK(access IN ('read','write')),PRIMARY KEY(project_id,user_id));
    ''')


def mode(db,pid):
    row=db.execute('SELECT mode FROM project_access WHERE project_id=?',(pid,)).fetchone()
    return row[0] if row else 'workspace' # Existing projects keep their explicit legacy scope.


def level(db,user,pid):
    if not pid:return 'read' if user['role']=='viewer' else 'write'
    project=db.execute('SELECT owner_id FROM projects WHERE id=?',(pid,)).fetchone()
    if not project:return None
    if user['role']=='admin' or project[0]==user['id']:return 'read' if user['role']=='viewer' else 'write'
    if mode(db,pid)=='workspace':return 'read' if user['role']=='viewer' else 'write'
    member=db.execute('SELECT access FROM project_members WHERE project_id=? AND user_id=?',(pid,user['id'])).fetchone()
    return ('read' if user['role']=='viewer' else member[0]) if member else None


def check(db,user,pid,write=False):
    if pid is not None and not isinstance(pid,str):raise ValueError('Geçersiz proje')
    if pid and not db.execute('SELECT 1 FROM projects WHERE id=?',(pid,)).fetchone():raise ValueError('Proje bulunamadı')
    grant=level(db,user,pid)
    if not grant or write and grant!='write':raise PermissionError(DENIED)


def manage(db,user,pid):
    row=db.execute('SELECT owner_id FROM projects WHERE id=?',(pid,)).fetchone()
    if not row or user['role']=='viewer' or user['role']!='admin' and row[0]!=user['id']:raise PermissionError('Proje erişimini yalnızca sahibi veya yönetici düzenleyebilir')


def sql(user,column='tasks.project_id'):
    # column is a fixed caller-owned SQL expression, never a request value.
    return f'''({column} IS NULL OR :scope_admin=1 OR EXISTS(
        SELECT 1 FROM projects scope_p LEFT JOIN project_access scope_a ON scope_a.project_id=scope_p.id
        WHERE scope_p.id={column} AND (scope_p.owner_id=:scope_user OR coalesce(scope_a.mode,'workspace')='workspace'
        OR EXISTS(SELECT 1 FROM project_members scope_m WHERE scope_m.project_id=scope_p.id AND scope_m.user_id=:scope_user))))''',{'scope_admin':int(user['role']=='admin'),'scope_user':user['id']}


def record(db,user,table,ident,write=False):
    row=db.execute('SELECT project_id FROM '+table+' WHERE id=?',(ident,)).fetchone()
    if not row:return False
    check(db,user,row[0],write)


def preflight(db,user,path,url,body=None):
    """Enforce project access on direct-ID paths before any report/materialization."""
    write=body is not None;query=parse_qs(urlparse(url).query);body=body or {};ident=body.get('id') or query.get('id',[''])[0]
    if path.startswith('/api/tasks/'):
        record(db,user,'tasks',path.split('/')[3],write)
    elif path.startswith('/api/approvals/'):
        row=db.execute('SELECT task_id FROM approvals WHERE id=?',(path.rsplit('/',1)[-1],)).fetchone()
        if row:record(db,user,'tasks',row[0],write)
    elif path.startswith('/api/files/') and path!='/api/files/search':
        record(db,user,'files',body.get('id') if write else path.rsplit('/',1)[-1],write)
    elif path=='/api/lab' and not write and ident:record(db,user,'lab_trials',ident)
    elif path in ('/api/lab/judge','/api/lab/delete'):record(db,user,'lab_trials',ident,True)
    elif path=='/api/lab/rate':
        row=db.execute('SELECT trial_id FROM lab_entries WHERE id=?',(ident,)).fetchone()
        if row:record(db,user,'lab_trials',row[0],True)
    elif path.startswith('/api/playbooks/runs') and ident:record(db,user,'playbook_runs',ident,write)
    elif path in ('/api/playbooks/start','/api/playbooks/toggle','/api/playbooks/delete'):record(db,user,'playbooks',ident,True)
    elif path=='/api/platform/thread':record(db,user,'tasks',query.get('task',[''])[0])
    elif path=='/api/platform/compare':
        for tid in query.get('ids',[''])[0].split(','):record(db,user,'tasks',tid)
    elif path=='/api/platform/knowledge/delete':record(db,user,'knowledge',ident,True)
    elif path in ('/api/templates/delete','/api/schedules/toggle','/api/schedules/delete'):
        table='templates' if path.startswith('/api/templates') else 'schedules'
        row=db.execute('SELECT config FROM '+table+' WHERE id=?',(ident,)).fetchone()
        if row:check(db,user,json.loads(row[0]).get('project_id') or None,True)
    elif path in ('/api/incidents/confidence','/api/incidents/actions'):
        record(db,user,'findings',body.get('finding_id'),True)
    elif path=='/api/incidents/actions/transition':
        row=db.execute('SELECT finding_id FROM incident_actions WHERE id=?',(ident,)).fetchone()
        if row:record(db,user,'findings',row[0],True)


def owner_can_run(server,db,tid):
    task=db.execute('SELECT project_id,owner_id FROM tasks WHERE id=?',(tid,)).fetchone()
    if not task:return False
    user=db.execute('SELECT * FROM users WHERE id=?',(task['owner_id'],)).fetchone()
    return bool(user and level(db,user,task['project_id'])=='write')


def handle_get(handler,user,path,db,server):
    if path!='/api/projects/members':return False
    pid=parse_qs(urlparse(handler.path).query).get('project_id',[''])[0];manage(db,user,pid)
    handler.respond(200,{'project_id':pid,'visibility':mode(db,pid),'users':[dict(r) for r in db.execute('SELECT id,name,role FROM users ORDER BY id')],
        'members':[dict(r) for r in db.execute('SELECT user_id,access FROM project_members WHERE project_id=? ORDER BY user_id',(pid,))]});return True


def handle_post(handler,user,path,body,server):
    if path!='/api/projects/members':return False
    with server.CONFIG_LOCK,server.connect() as db:
        db.execute('BEGIN IMMEDIATE');pid=body.get('project_id');manage(db,user,pid)
        uid=body.get('user_id');grant=body.get('access')
        if not isinstance(uid,str) or grant not in ('read','write','none') or not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():raise ValueError('Proje üyesi veya erişim düzeyi geçersiz')
        owner=db.execute('SELECT owner_id FROM projects WHERE id=?',(pid,)).fetchone()[0]
        if uid==owner or uid=='admin':raise ValueError('Proje sahibi ve yönetici erişimi üyelikle değiştirilemez')
        if grant=='none':db.execute('DELETE FROM project_members WHERE project_id=? AND user_id=?',(pid,uid))
        else:db.execute('INSERT OR REPLACE INTO project_members VALUES(?,?,?)',(pid,uid,grant))
        cancel_disallowed(server,db,pid,user['id'])
        server.log(db,None,'project_membership_changed',pid+' · '+uid+' · '+grant+' · '+user['id'])
    handler.respond(200,{'ok':True});return True


def cancel_disallowed(server,db,pid,actor):
    for row in db.execute("SELECT id FROM tasks WHERE project_id=? AND status IN ('queued','running','awaiting_approval')",(pid,)).fetchall():
        if owner_can_run(server,db,row['id']):continue
        db.execute("UPDATE tasks SET status='cancelled',error=?,updated_at=? WHERE id=?",(DENIED,server.now(),row['id']))
        db.execute("UPDATE approvals SET status='rejected',decided_by=?,decided_at=? WHERE task_id=? AND status='pending'",(actor,server.now(),row['id']))
        db.execute("UPDATE mcp_calls SET status='rejected',updated_at=? WHERE task_id=? AND status IN ('pending','approved')",(server.now(),row['id']))
        server.log(db,row['id'],'project_access_revoked',actor)
