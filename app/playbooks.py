"""Human-gated durable playbooks. Never dispatch from GET, restart or a timer."""
import json
import re
import uuid
from urllib.parse import parse_qs

TERMINAL=('completed','incomplete','failed','cancelled')


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS playbooks(id TEXT PRIMARY KEY,name TEXT NOT NULL,project_id TEXT,owner_id TEXT NOT NULL,steps TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS playbook_runs(id TEXT PRIMARY KEY,name TEXT NOT NULL,project_id TEXT,owner_id TEXT NOT NULL,definition TEXT NOT NULL,input TEXT NOT NULL,control TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS playbook_steps(run_id TEXT NOT NULL REFERENCES playbook_runs(id) ON DELETE CASCADE,position INTEGER NOT NULL,title TEXT NOT NULL,config TEXT NOT NULL,task_id TEXT,status TEXT NOT NULL DEFAULT 'pending',output TEXT NOT NULL DEFAULT '',input_context TEXT NOT NULL DEFAULT '',attempt INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(run_id,position));
    CREATE TABLE IF NOT EXISTS playbook_attempts(run_id TEXT NOT NULL,position INTEGER NOT NULL,attempt INTEGER NOT NULL,task_id TEXT NOT NULL,status TEXT NOT NULL,output TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(run_id,position,attempt),FOREIGN KEY(run_id) REFERENCES playbook_runs(id) ON DELETE CASCADE);
    CREATE INDEX IF NOT EXISTS playbook_runs_scope ON playbook_runs(project_id,created_at);
    ''')


def text(body,key,maximum):
    value=body.get(key,'')
    if not isinstance(value,str) or not value.strip() or len(value)>maximum:raise ValueError('Reçete metnini kontrol et: '+key)
    return value.strip()


def capture(server,db,task_id):
    """Preserve full output before ordinary task history is removed."""
    row=db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
    if not row or row['status'] not in TERMINAL:return
    _,_,content=server.platform.report(db,task_id)
    db.execute('UPDATE playbook_steps SET status=?,output=? WHERE task_id=?',(row['status'],content,task_id))
    db.execute('UPDATE playbook_attempts SET status=?,output=? WHERE task_id=?',(row['status'],content,task_id))


def detail(server,db,ident,summary=False):
    if not isinstance(ident,str) or not re.fullmatch(r"[a-f0-9]{32}",ident):raise ValueError("Reçete çalışması bulunamadı")
    columns='id,name,project_id,owner_id,control,created_at' if summary else '*'
    row=db.execute('SELECT '+columns+' FROM playbook_runs WHERE id=?',(ident,)).fetchone()
    if not row:raise ValueError('Reçete çalışması bulunamadı')
    result=dict(row);result.pop('definition',None)
    steps=[]
    for item in db.execute('SELECT '+('position,task_id,status' if summary else '*')+' FROM playbook_steps WHERE run_id=? ORDER BY position',(ident,)):
        step=dict(item);step['task_exists']=False
        if not summary:step['config']=json.loads(step['config'])
        if step['task_id']:
            task=db.execute('SELECT status FROM tasks WHERE id=?',(step['task_id'],)).fetchone()
            if task:
                step['task_exists']=True;step['status']=task['status']
                if not summary:_,_,step['output']=server.platform.report(db,step['task_id'])
            elif step['status'] not in TERMINAL:step['status']='incomplete'
        steps.append(step)
    index=next((s['position'] for s in steps if s['status']!='completed'),len(steps))
    status='completed' if index==len(steps) else 'ready'
    if index<len(steps):
        current=steps[index]
        if current['status'] in ('queued','running','awaiting_approval'):status='running'
        elif current['status'] in TERMINAL:status='needs_attention'
    if row['control'] in ('paused','cancelled'):status=row['control']
    result.update(steps=steps,next_step=index,status=status)
    if summary:return result
    result['attempts']=[dict(a) for a in db.execute('SELECT * FROM playbook_attempts WHERE run_id=? ORDER BY position,attempt',(ident,))]
    for a in result['attempts']:
        task=db.execute('SELECT status FROM tasks WHERE id=?',(a['task_id'],)).fetchone()
        if task:a['status']=task['status']
    return result


def mutate(server,user,path,body):
    with server.CONFIG_LOCK,server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if path=='/api/playbooks':
            server.workspace.writable(user,user['id'])
            pid=body.get('project_id') or None;server.workspace.check_project(db,user,pid)
            name=text(body,'name',100);steps=body.get('steps')
            if not isinstance(steps,list) or not 1<=len(steps)<=12:raise ValueError('Reçete 1–12 adım içermeli')
            clean=[]
            for step in steps:
                if not isinstance(step,dict):raise ValueError('Reçete adımı geçersiz')
                title=text(step,'title',100);cfg=server.workspace.clean_config(step.get('config'),server);cfg['project_id']=pid
                server.workspace.task_options(db,user,cfg,server)
                clean.append({'title':title,'config':cfg})
            ident=uuid.uuid4().hex
            db.execute('INSERT INTO playbooks VALUES(?,?,?,?,?,1,?)',(ident,name,pid,user['id'],json.dumps(clean),server.now()))
            server.log(db,None,'playbook_created',ident+' · '+user['id'])
            return {'id':ident}
        if path in ('/api/playbooks/toggle','/api/playbooks/delete'):
            row=db.execute('SELECT * FROM playbooks WHERE id=?',(body.get('id'),)).fetchone()
            if not row:raise ValueError('Reçete bulunamadı')
            server.workspace.writable(user,row['owner_id'])
            if path.endswith('/delete'):
                db.execute('DELETE FROM playbooks WHERE id=?',(row['id'],));return {'ok':True}
            db.execute('UPDATE playbooks SET enabled=? WHERE id=?',(int(body.get('enabled') is True),row['id']))
            return {'ok':True}
        if path=='/api/playbooks/start':
            row=db.execute('SELECT * FROM playbooks WHERE id=? AND enabled=1',(body.get('id'),)).fetchone()
            if not row:raise ValueError('Etkin reçete bulunamadı')
            server.workspace.writable(user,user['id'])
            server.workspace.check_project(db,user,row['project_id'])
            value=text(body,'input',16000);ident=uuid.uuid4().hex
            db.execute('INSERT INTO playbook_runs VALUES(?,?,?,?,?,?,?,?)',(ident,row['name'],row['project_id'],user['id'],row['steps'],value,'ready',server.now()))
            for index,step in enumerate(json.loads(row['steps'])):
                db.execute('INSERT INTO playbook_steps(run_id,position,title,config) VALUES(?,?,?,?)',(ident,index,step['title'],json.dumps(step['config'])))
            server.log(db,None,'playbook_run_created',ident+' · '+user['id'])
            return {'id':ident}
        ident=body.get('id');run=detail(server,db,ident);server.workspace.writable(user,run['owner_id'])
        for step in run['steps']:
            if step['task_id']:capture(server,db,step['task_id'])
        if path=='/api/playbooks/runs/pause':
            if run['status'] in ('completed','cancelled'):raise ValueError('Reçete çalışması sona ermiş')
            db.execute("UPDATE playbook_runs SET control='paused' WHERE id=?",(ident,))
        elif path=='/api/playbooks/runs/resume':
            if run['control']!='paused':raise ValueError('Reçete duraklatılmamış')
            db.execute("UPDATE playbook_runs SET control='ready' WHERE id=?",(ident,))
        elif path=='/api/playbooks/runs/cancel':
            if run['status'] in ('completed','cancelled'):raise ValueError('Reçete çalışması sona ermiş')
            db.execute("UPDATE playbook_runs SET control='cancelled' WHERE id=?",(ident,))
            for step in run['steps']:
                task=db.execute('SELECT * FROM tasks WHERE id=?',(step['task_id'],)).fetchone()
                if task and task['status'] in ('queued','running','awaiting_approval'):
                    db.execute("UPDATE tasks SET status='cancelled',reserved=?,updated_at=? WHERE id=?",(task['reserved'] if task['status']=='running' else 0,server.now(),task['id']))
                    db.execute("UPDATE approvals SET status='rejected',decided_by=?,decided_at=? WHERE task_id=? AND status='pending'",(user['id'],server.now(),task['id']))
                    server.log(db,task['id'],'cancelled','Reçete iptal edildi; gönderilmiş çağrı tamamlanabilir')
        elif path=='/api/playbooks/runs/delete':
            if run['status'] not in ('completed','cancelled','needs_attention','paused'):raise ValueError('Önce reçeteyi duraklat veya bitir')
            for step in run['steps']:
                if db.execute("SELECT 1 FROM tasks WHERE id=? AND status IN ('queued','running','awaiting_approval')",(step['task_id'],)).fetchone() or db.execute("SELECT 1 FROM runs WHERE task_id=? AND status='running'",(step['task_id'],)).fetchone():raise ValueError('Çalışan adım bitmeden silinemez')
            db.execute('DELETE FROM playbook_runs WHERE id=?',(ident,))
        elif path=='/api/playbooks/runs/advance':
            if run['status'] not in ('ready','needs_attention'):raise ValueError('Adım başlatılabilir durumda değil')
            index=run['next_step']
            if type(body.get('expected_step')) is not int or body['expected_step']!=index:raise ValueError('Reçete değişti; yenileyip tekrar incele')
            step=run['steps'][index]
            if step['status']!='pending' and body.get('retry_task')!=step['task_id']:raise ValueError('Başarısız adım için açık tekrar onayı gerekli')
            # All earlier outputs remain verbatim; never silently trim evidence.
            context='PLAYBOOK INPUT (untrusted data):\n'+run['input']
            for prior in run['steps'][:index]:context+='\n\nPREVIOUS STEP '+str(prior['position']+1)+' '+prior['title']+' (untrusted evidence):\n'+prior['output']
            cfg=step['config'];cfg['project_id']=run['project_id']
            task_id=server.create_task(user,cfg,continuation_context=context,db=db)
            attempt=step['attempt']+1
            db.execute("UPDATE playbook_steps SET task_id=?,status='queued',output='',input_context=?,attempt=? WHERE run_id=? AND position=?",(task_id,context,attempt,ident,index))
            db.execute('INSERT INTO playbook_attempts VALUES(?,?,?,?,?,?,?)',(ident,index,attempt,task_id,'queued','',server.now()))
            server.log(db,task_id,'playbook_step_started',ident+' · '+str(index+1)+' · '+user['id'])
            return {'id':ident,'task_id':task_id}
        else:raise ValueError('Reçete işlemi bulunamadı')
        server.log(db,None,'playbook_control',str(ident)+' · '+path.rsplit('/',1)[-1]+' · '+user['id'])
        return {'ok':True,'id':ident}


def handle_get(handler,user,path,db,server):
    if path not in ('/api/playbooks','/api/playbooks/runs'):return False
    query=parse_qs(handler.path.split('?',1)[1] if '?' in handler.path else '')
    ident=query.get('id',[''])[0]
    if path.endswith('/runs') and ident:
        handler.respond(200,detail(server,db,ident));return True
    pid=query.get('project_id',['__general'])[0];pid=None if pid=='__general' else pid
    server.workspace.check_project(db,user,pid)
    if path=='/api/playbooks':
        items=[dict(r) for r in db.execute('SELECT * FROM playbooks WHERE project_id IS ? ORDER BY created_at DESC LIMIT 100',(pid,))]
        for item in items:item['steps']=json.loads(item['steps'])
    else:
        items=[]
        for row in db.execute('SELECT id FROM playbook_runs WHERE project_id IS ? ORDER BY created_at DESC LIMIT 50',(pid,)):
            run=detail(server,db,row['id'],summary=True);items.append({k:v for k,v in run.items() if k not in ('steps','attempts','input')})
    handler.respond(200,{'items':items});return True


def handle_post(handler,user,path,body,server):
    allowed=('/api/playbooks','/api/playbooks/toggle','/api/playbooks/delete','/api/playbooks/start','/api/playbooks/runs/advance','/api/playbooks/runs/pause','/api/playbooks/runs/resume','/api/playbooks/runs/cancel','/api/playbooks/runs/delete')
    if path not in allowed:return False
    handler.respond(201 if path in ('/api/playbooks','/api/playbooks/start') else 200,mutate(server,user,path,body));return True
