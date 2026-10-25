"""Opt-in comparable model trials with human ratings and optional separate judge."""
import datetime as dt
import json
import secrets
import uuid
from urllib.parse import parse_qs,urlparse


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS lab_trials(id TEXT PRIMARY KEY,name TEXT NOT NULL,prompt TEXT NOT NULL,project_id TEXT,owner_id TEXT NOT NULL,created_at TEXT NOT NULL,judge_task TEXT);
    CREATE TABLE IF NOT EXISTS lab_entries(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL REFERENCES lab_trials(id) ON DELETE CASCADE,label TEXT NOT NULL,agent_snapshot TEXT NOT NULL,task_id TEXT NOT NULL,rating TEXT,notes TEXT NOT NULL DEFAULT '',snapshot TEXT NOT NULL DEFAULT '');''')


def result(server,db,entry):
    row=db.execute('SELECT * FROM tasks WHERE id=?',(entry['task_id'],)).fetchone()
    if not row:return json.loads(entry['snapshot']) if entry['snapshot'] else {'status':'missing','output':'','cost':0,'seconds':None,'input_tokens':0,'output_tokens':0}
    task,runs,output=server.platform.report(db,row['id'])
    terminal=task['status'] in ('completed','incomplete','failed','cancelled')
    seconds=(dt.datetime.fromisoformat(task['updated_at'])-dt.datetime.fromisoformat(task['created_at'])).total_seconds() if terminal else None
    return {'status':task['status'],'output':output,'cost':task['cost'],'seconds':max(0,seconds) if seconds is not None else None,'input_tokens':sum(r['input_tokens'] or 0 for r in runs),'output_tokens':sum(r['output_tokens'] or 0 for r in runs),'cost_estimated':bool(task['cost_estimated'])}


def capture(server,db,tid):
    for row in db.execute('SELECT * FROM lab_entries WHERE task_id=?',(tid,)).fetchall():db.execute('UPDATE lab_entries SET snapshot=? WHERE id=?',(json.dumps(result(server,db,row)),row['id']))


def detail(server,db,ident):
    trial=db.execute('SELECT * FROM lab_trials WHERE id=?',(ident,)).fetchone()
    if not trial:raise ValueError('Model denemesi bulunamadı')
    data=dict(trial);entries=[]
    for row in db.execute('SELECT * FROM lab_entries WHERE trial_id=? ORDER BY label',(ident,)):
        entry=dict(row);entry.pop('snapshot');entry['agent_snapshot']=json.loads(entry['agent_snapshot']);entry['rating']=json.loads(entry['rating']) if entry['rating'] else None;entry.update(result(server,db,row));entries.append(entry)
    data['entries']=entries;return data


def handle_get(handler,user,path,db,server):
    if path!='/api/lab':return False
    query=parse_qs(urlparse(handler.path).query)
    if query.get('id'):data=detail(server,db,query['id'][0])
    else:
        pid=query.get('project_id',['__general'])[0];pid=None if pid=='__general' else pid;server.workspace.check_project(db,user,pid)
        data={'items':[dict(r) for r in db.execute('SELECT id,name,owner_id,project_id,created_at FROM lab_trials WHERE project_id IS ? ORDER BY created_at DESC LIMIT 100',(pid,))]}
    handler.respond(200,data);return True


def handle_post(handler,user,path,body,server):
    if path not in ('/api/lab','/api/lab/rate','/api/lab/judge','/api/lab/delete'):return False
    if user['role']=='viewer':raise PermissionError('Gözlemci deneme başlatamaz veya puanlayamaz')
    with server.CONFIG_LOCK,server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if path=='/api/lab':
            cfg=server.workspace.clean_config(body.get('config'),server);agents=body.get('agents')
            if not isinstance(agents,list) or not 2<=len(agents)<=4 or any(not isinstance(a,str) or a not in server.BY_ID for a in agents) or len(set(agents))!=len(agents):raise ValueError('Deneme için 2–4 farklı ajan seç')
            ident=uuid.uuid4().hex;name=server.platform.text(body,'name',100);pid=cfg.get('project_id') or None
            db.execute('INSERT INTO lab_trials VALUES(?,?,?,?,?,?,NULL)',(ident,name,cfg['prompt'],pid,user['id'],server.now()))
            shared_seed=secrets.token_hex(32)
            for index,aid in enumerate(agents):
                one=dict(cfg,mode='single',agents=[aid],judge=None,fallback_agent=None,web_enabled=False,mcp_servers=[],tool_requested=False,review_enabled=False)
                tid=server.create_task(user,one,db=db)
                if one.get('mask_sensitive') is True:db.execute('UPDATE tasks SET privacy_seed=? WHERE id=?',(shared_seed,tid))
                snapshot={k:server.BY_ID[aid][k] for k in ('id','name','model','provider')}
                db.execute('INSERT INTO lab_entries(id,trial_id,label,agent_snapshot,task_id) VALUES(?,?,?,?,?)',(uuid.uuid4().hex,ident,chr(65+index),json.dumps(snapshot),tid))
            server.log(db,None,'lab_created',ident+' · '+user['id']);data={'id':ident}
        elif path=='/api/lab/rate':
            entry=db.execute('SELECT e.*,t.owner_id FROM lab_entries e JOIN lab_trials t ON t.id=e.trial_id WHERE e.id=?',(body.get('id'),)).fetchone()
            if not entry:raise ValueError('Deneme çıktısı bulunamadı')
            server.workspace.writable(user,entry['owner_id'])
            if result(server,db,entry)['status'] not in ('completed','incomplete','failed','cancelled'):raise ValueError('Puanlamadan önce denemenin bitmesini bekle')
            scores=body.get('rating')
            if not isinstance(scores,dict) or set(scores)!=set(('accuracy','evidence','usefulness')) or any(type(v) is not int or not 0<=v<=5 for v in scores.values()):raise ValueError('Her ölçütü 0–5 arasında puanla')
            notes=server.platform.text(body,'notes',4000,False)
            db.execute('UPDATE lab_entries SET rating=?,notes=? WHERE id=?',(json.dumps(scores),notes,entry['id']));server.log(db,None,'lab_rated',entry['id']+' · '+user['id']);data={'ok':True}
        else:
            trial=detail(server,db,body.get('id'));server.workspace.writable(user,trial['owner_id'])
            if path.endswith('/delete'):
                if trial['judge_task'] and db.execute("SELECT 1 FROM tasks WHERE id=? AND status IN ('queued','running','awaiting_approval')",(trial['judge_task'],)).fetchone():raise ValueError('Çalışan deneme bitmeden silinemez')
                if any(e['status'] in ('queued','running','awaiting_approval') for e in trial['entries']):raise ValueError('Çalışan deneme bitmeden silinemez')
                db.execute('DELETE FROM lab_trials WHERE id=?',(trial['id'],));data={'ok':True}
            else:
                if trial['judge_task'] or any(e['status']!='completed' for e in trial['entries']):raise ValueError('Önce tüm denemeler tamamlanmalı; baş uzman bir kez başlatılabilir')
                aid=body.get('agent');context='ORIGINAL TASK (untrusted):\n'+trial['prompt']+'\n\n'+'\n\n'.join('OUTPUT '+e['label']+' (untrusted evidence):\n'+e['output'] for e in trial['entries'])
                cfg={'prompt':'A, B, C, D etiketli çıktıları doğruluk, kaynak/kanıt ve kullanışlılık yönünden karşılaştır. Her kararın gerekçesini, belirsizlikleri ve doğrulama adımlarını yaz. Etiketlerde olmayan sonuç üretme. Bu değerlendirme nesnel doğruluk garantisi değildir.','agents':[aid],'mode':'single','project_id':trial['project_id'],'mask_sensitive':True}
                tid=server.create_task(user,cfg,continuation_context=context,db=db);db.execute('UPDATE lab_trials SET judge_task=? WHERE id=?',(tid,trial['id']));data={'task_id':tid}
        server.log(db,None,'lab_action',path+' · '+user['id'])
    handler.respond(201 if path=='/api/lab' else 200,data);return True
