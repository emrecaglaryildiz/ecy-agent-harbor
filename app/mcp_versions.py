"""Administrative MCP revision metadata, without endpoint, secrets, schemas or arguments."""
import datetime as dt
import hashlib
import json
import re
from .audit import safe_actor


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mcp_versions(
        server_id TEXT NOT NULL,revision INTEGER NOT NULL,reason TEXT NOT NULL,
        actor_id TEXT NOT NULL,created_at TEXT NOT NULL,enabled INTEGER NOT NULL,
        tools TEXT NOT NULL,PRIMARY KEY(server_id,revision))''')
    # Existing connections start at an explicit baseline, never fabricated history.
    for row in db.execute('SELECT * FROM mcp_servers').fetchall():
        if not db.execute('SELECT 1 FROM mcp_versions WHERE server_id=?',(row['id'],)).fetchone():
            record(db,row['id'],'baseline','system',dt.datetime.now(dt.timezone.utc).isoformat(),invalidate=False)


def record(db,identifier,reason,actor,stamp,invalidate=True):
    row=db.execute('SELECT * FROM mcp_servers WHERE id=?',(identifier,)).fetchone()
    tools=[{'name':tool['name'],'schema_hash':fingerprint(tool['inputSchema']),
            'description_hash':fingerprint(tool.get('description','')),
            'declared_read_only':bool(tool.get('declared_read_only')),
            'policy':tool['policy']} for tool in json.loads(row['tools'])]
    db.execute('INSERT INTO mcp_versions VALUES(?,?,?,?,?,?,?)',
               (identifier,row['revision'],reason,safe_actor(actor),stamp,row['enabled'],json.dumps(tools)))
    if invalidate:
        db.execute("UPDATE mcp_calls SET status='rejected',error='MCP sürümü değişti; çağrıyı yeniden oluştur',updated_at=? WHERE server_id=? AND revision!=? AND status IN ('pending','approved')",(stamp,identifier,row['revision']))


def expected(row,body):
    value=body.get('expected_revision')
    if 'expected_revision' in body and (type(value) is not int or value!=(row['revision'] if row else 0)):
        raise ValueError('MCP sürümü değişti; ekranı yenile')


def guard(db,row):
    live=db.execute('SELECT revision FROM mcp_servers WHERE id=?',(row['id'],)).fetchone()
    if not live or live[0]!=row['revision']:
        raise ValueError('MCP sürümü değişti; ekranı yenile')


def history(db,identifier,before=None):
    if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('MCP kimliği geçersiz')
    row=db.execute('SELECT revision FROM mcp_servers WHERE id=?',(identifier,)).fetchone()
    if not row:raise ValueError('MCP sunucusu bulunamadı')
    if before is not None and (type(before) is not int or before<1):raise ValueError('MCP geçmiş sayfası geçersiz')
    args=(identifier,)+( (before,) if before is not None else () )
    rows=db.execute('SELECT * FROM mcp_versions WHERE server_id=?'+(' AND revision<?' if before is not None else '')+' ORDER BY revision DESC LIMIT 21',args).fetchall()
    items=[]
    for r in rows[:20]:
        item=dict(r);item['tools']=json.loads(item['tools']);item['enabled']=bool(item['enabled']);items.append(item)
    return {'server_id':identifier,'current_revision':row[0],'items':items,'next_before':items[-1]['revision'] if len(rows)>20 else None}
