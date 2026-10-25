"""Local log triage and human action tracking; no model or external writes."""
import datetime as dt
import hashlib
import itertools
import json
import re
import uuid
from urllib.parse import parse_qs,urlparse

STAMP=re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?')
LINES=re.compile(r'[^\r\n\v\f\x1c-\x1e\x85\u2028\u2029]*(?:\r\n|[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]|$)')
LEVEL=re.compile(r'\b(critical|fatal|error|warn(?:ing)?|info|debug)\b',re.I)


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS finding_reviews(finding_id TEXT PRIMARY KEY REFERENCES findings(id) ON DELETE CASCADE,confidence TEXT NOT NULL,actor TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS incident_actions(id TEXT PRIMARY KEY,finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,owner_id TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS incident_action_events(id INTEGER PRIMARY KEY,action_id TEXT NOT NULL REFERENCES incident_actions(id) ON DELETE CASCADE,actor TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    ''')


def window(db,pid,file_id,offset=0,limit=1000):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=2000:raise ValueError('Log sayfası geçersiz')
    row=db.execute('SELECT * FROM files WHERE id=? AND project_id IS ?',(file_id,pid)).fetchone()
    if not row or row['kind']!='text':raise ValueError('Seçili projeden metin/log dosyası gerekli')
    groups={};scanned=0;more=False
    for number,line in itertools.islice(enumerate((m.group().rstrip('\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029') for m in LINES.finditer(row['text']) if m.start()<len(row['text'])),1),offset,offset+limit+1):
        if scanned==limit:more=True;break
        scanned+=1;line=line.rstrip('\r\n');match=STAMP.search(line[:512]);timestamp=None
        if match:
            try:
                parsed=dt.datetime.fromisoformat(match.group().replace('Z','+00:00'))
                # Never silently guess the timezone of source records.
                if parsed.tzinfo:timestamp=parsed.astimezone(dt.timezone.utc).isoformat()
            except ValueError:pass
        normalized=(line[:match.start()]+line[match.end():]) if match else line
        # Exact text except timestamp and edge whitespace; preserve IPs/IDs/numbers.
        normalized=normalized.strip();key=hashlib.sha256(normalized.encode()).hexdigest()
        level=LEVEL.search(line[:512]);level=level.group().lower() if level else 'unknown'
        if key not in groups:groups[key]={'key':key,'excerpt':normalized[:1000],'excerpt_shortened':len(normalized)>1000,'level':level,'lines':[],'first_at':None,'last_at':None,'untimed':0}
        g=groups[key];g['lines'].append(number)
        if not timestamp:g['untimed']+=1
        if timestamp:g['first_at']=min(g['first_at'],timestamp) if g['first_at'] else timestamp;g['last_at']=max(g['last_at'],timestamp) if g['last_at'] else timestamp
    return {'file_id':file_id,'file_name':row['name'],'offset':offset,'scanned':scanned,'next_offset':offset+scanned if more else None,'groups':sorted(groups.values(),key=lambda x:(x['first_at'] is None,x['first_at'] or '',x['lines'][0]))}


def handle_get(handler,user,path,db,server):
    if path not in ('/api/incidents','/api/incidents/log'):return False
    query=parse_qs(urlparse(handler.path).query);pid=query.get('project_id',['__general'])[0];pid=None if pid=='__general' else pid
    server.workspace.check_project(db,user,pid)
    if path.endswith('/log'):
        result=window(db,pid,query.get('file_id',[''])[0],int(query.get('offset',['0'])[0]),int(query.get('limit',['1000'])[0]))
    else:
        reviews=[dict(r) for r in db.execute('SELECT r.* FROM finding_reviews r JOIN findings f ON f.id=r.finding_id WHERE f.project_id IS ?',(pid,))]
        actions=[dict(r) for r in db.execute('SELECT a.* FROM incident_actions a JOIN findings f ON f.id=a.finding_id WHERE f.project_id IS ? ORDER BY a.created_at DESC',(pid,))]
        for a in actions:a['history']=[dict(e) for e in db.execute('SELECT actor,status,created_at FROM incident_action_events WHERE action_id=? ORDER BY id',(a['id'],))]
        events=[dict(r) for r in db.execute('SELECT e.id,e.finding_id,e.actor,e.created_at,e.snapshot FROM finding_events e JOIN findings f ON f.id=e.finding_id WHERE f.project_id IS ? ORDER BY e.created_at DESC,e.id DESC LIMIT 200',(pid,))]
        for e in events:e['snapshot']=json.loads(e['snapshot'])
        result={'reviews':reviews,'actions':actions,'timeline':events}
    handler.respond(200,result);return True


def handle_post(handler,user,path,body,server):
    if path not in ('/api/incidents/confidence','/api/incidents/actions','/api/incidents/actions/transition'):return False
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if path.endswith('/transition'):
            action=db.execute('SELECT * FROM incident_actions WHERE id=?',(body.get('id'),)).fetchone()
            if not action:raise ValueError('Aksiyon bulunamadı')
            server.workspace.writable(user,action['owner_id'])
            status=body.get('status')
            allowed={'proposed':('approved','dismissed'),'approved':('done','dismissed'),'done':(),'dismissed':()}
            if status not in allowed[action['status']] or body.get('expected_status')!=action['status']:raise ValueError('Aksiyon değişti veya geçiş geçersiz')
            ident=action['id'];db.execute('UPDATE incident_actions SET status=?,updated_at=? WHERE id=?',(status,server.now(),ident))
        else:
            finding=db.execute('SELECT * FROM findings WHERE id=?',(body.get('finding_id'),)).fetchone()
            if not finding:raise ValueError('Bulgu bulunamadı')
            server.workspace.writable(user,finding['owner_id'])
            if path.endswith('/confidence'):
                value=body.get('confidence')
                if value not in ('unassessed','low','medium','high'):raise ValueError('Güven düzeyi geçersiz')
                db.execute('INSERT OR REPLACE INTO finding_reviews VALUES(?,?,?,?)',(finding['id'],value,user['id'],server.now()))
                server.log(db,None,'finding_confidence',finding['id']+' · '+value+' · '+user['id'])
                db.commit();handler.respond(200,{'ok':True});return True
            title=server.platform.text(body,'title',500);ident=uuid.uuid4().hex;status='proposed'
            db.execute('INSERT INTO incident_actions VALUES(?,?,?,?,?,?,?)',(ident,finding['id'],user['id'],title,status,server.now(),server.now()))
        db.execute('INSERT INTO incident_action_events(action_id,actor,status,created_at) VALUES(?,?,?,?)',(ident,user['id'],status,server.now()))
        server.log(db,None,'incident_action',ident+' · '+status+' · '+user['id'])
    handler.respond(200,{'id':ident,'status':status});return True
