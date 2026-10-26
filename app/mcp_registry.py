"""Explicit connection -> tool policy -> task selection, enforced at every call."""
import hashlib
import json
import re
import time
import uuid
from .mcp_client import Client,manifest,endpoint
from . import mcp_versions
from urllib.parse import parse_qs,urlparse
from jsonschema import Draft202012Validator


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS mcp_servers(id TEXT PRIMARY KEY,name TEXT NOT NULL,config TEXT NOT NULL,enabled INTEGER NOT NULL,tools TEXT NOT NULL,revision INTEGER NOT NULL,checked_at TEXT,error TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS mcp_calls(id TEXT PRIMARY KEY,server_id TEXT NOT NULL,tool TEXT NOT NULL,owner_id TEXT NOT NULL,task_id TEXT,arguments TEXT NOT NULL,status TEXT NOT NULL,result TEXT,error TEXT,revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);''')
    mcp_versions.initialize(db)
    db.execute("UPDATE mcp_calls SET status='unknown',error='Sunucu yeniden başladı; çağrı otomatik tekrarlanmadı' WHERE status IN ('running','approved','dispatched')")
    if 'mcp_selection' not in {r[1] for r in db.execute('PRAGMA table_info(tasks)')}:
        db.execute("ALTER TABLE tasks ADD COLUMN mcp_selection TEXT NOT NULL DEFAULT '[]'")


def public(row,admin=False):
    cfg=json.loads(row['config']);cfg.pop('encrypted_token',None)
    if not admin:cfg={k:cfg[k] for k in ('transport',) if k in cfg}
    return {'id':row['id'],'name':row['name'],'config':cfg,'has_token':bool(json.loads(row['config']).get('encrypted_token')) if admin else False,'enabled':bool(row['enabled']),'tools':json.loads(row['tools']),'revision':row['revision'],'checked_at':row['checked_at'],'error':row['error']}


def connection(server,row):
    cfg=json.loads(row['config']);secret=cfg.get('encrypted_token')
    return Client(cfg,server.decrypt(server.DB_PATH,secret) if secret else '')


def valid_tools(tools,previous=None):
    result=[];seen=set();previous={t['name']:t for t in previous or []}
    for item in tools:
        if not isinstance(item,dict) or not isinstance(item.get('name'),str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',item['name']) or item['name'] in seen:raise ValueError('MCP araç tanımı geçersiz')
        schema=item.get('inputSchema',{})
        if not isinstance(schema,dict) or schema.get('type')!='object' or len(json.dumps(schema))>64000:raise ValueError('MCP araç şeması geçersiz')
        # Remote references can silently create outbound requests during validation.
        def inspect(value):
            if isinstance(value,dict):
                if any(k in value for k in ('$ref','$dynamicRef','$recursiveRef','pattern','patternProperties')):raise ValueError('MCP şemasında referans ve regex bu sürümde desteklenmiyor')
                for child in value.values():inspect(child)
            elif isinstance(value,list):
                for child in value:inspect(child)
        def depth(value,level=0):
            if level>12:raise ValueError('MCP şema derinliği fazla')
            if isinstance(value,dict):
                for child in value.values():depth(child,level+1)
            elif isinstance(value,list):
                for child in value:depth(child,level+1)
        depth(schema);inspect(schema);Draft202012Validator.check_schema(schema)
        old=previous.get(item['name'],{})
        same=old.get('inputSchema')==schema and old.get('description','')==str(item.get('description',''))[:2000] and old.get('declared_read_only',False)==(item.get('annotations',{}).get('readOnlyHint') is True)
        result.append({'name':item['name'],'description':str(item.get('description',''))[:2000],'inputSchema':schema,'policy':old.get('policy','blocked') if same else 'blocked','declared_read_only':item.get('annotations',{}).get('readOnlyHint') is True})
        seen.add(item['name'])
    return result


def selection(server,db,user,body):
    ids=body.get('mcp_servers',[])
    if not isinstance(ids,list) or len(ids)>8 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):raise ValueError('MCP seçimi geçersiz')
    result=[]
    for ident in ids:
        row=db.execute('SELECT * FROM mcp_servers WHERE id=? AND enabled=1',(ident,)).fetchone()
        if not row:raise ValueError('MCP sunucusu etkin değil')
        tools=[t for t in json.loads(row['tools']) if t['policy']=='task_read']
        if not tools:raise ValueError('Bu MCP için görevlerde izin verilen araç yok')
        result.append({'id':ident,'revision':row['revision']})
    return result


def execute(server,ident):
    # An approved call cannot be replayed after crash or concurrent approval.
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        call=db.execute('SELECT * FROM mcp_calls WHERE id=?',(ident,)).fetchone()
        if not call or call['status']!='approved':raise ValueError('MCP çağrısı onaylı değil')
        if call['task_id'] and not server.access.owner_can_run(server,db,call['task_id']):raise PermissionError(server.access.DENIED)
        row=db.execute('SELECT * FROM mcp_servers WHERE id=? AND enabled=1',(call['server_id'],)).fetchone()
        tool=next((t for t in json.loads(row['tools']) if t['name']==call['tool']),None) if row else None
        if not row or row['revision']!=call['revision'] or not tool or tool['policy']=='blocked':raise ValueError('MCP izni değişti; yeni çağrı oluştur')
        db.execute("UPDATE mcp_calls SET status='running',updated_at=? WHERE id=?",(server.now(),ident))
    client=None
    try:
        client=connection(server,row)
        current=next((t for t in client.tools() if t.get('name')==call['tool']),None)
        if not current or current.get('inputSchema')!=tool['inputSchema'] or str(current.get('description',''))[:2000]!=tool['description'] or (current.get('annotations',{}).get('readOnlyHint') is True)!=tool.get('declared_read_only',False):
            raise ValueError('MCP araç tanımı değişti; tekrar keşfet')
        with server.connect() as dispatch:
            dispatch.execute('BEGIN IMMEDIATE')
            if call['task_id'] and not server.access.owner_can_run(server,dispatch,call['task_id']):raise PermissionError(server.access.DENIED)
            live=dispatch.execute('SELECT enabled,revision FROM mcp_servers WHERE id=?',(call['server_id'],)).fetchone()
            status_row=dispatch.execute('SELECT status FROM mcp_calls WHERE id=?',(ident,)).fetchone()
            if not live or not live['enabled'] or live['revision']!=call['revision'] or status_row[0]!='running':raise PermissionError('MCP izni çağrıdan önce iptal edildi')
            dispatch.execute("UPDATE mcp_calls SET status='dispatched',updated_at=? WHERE id=?",(server.now(),ident))
        result=client.call(call['tool'],json.loads(call['arguments']))
        status='failed' if result.get('isError') else 'completed'
        encoded=json.dumps(result,ensure_ascii=False);error=None
    except Exception:
        status='failed';encoded=None;error='MCP çağrısı tamamlanamadı; bağlantıyı ve izinleri kontrol et'
    finally:
        if client:client.close()
    with server.connect() as db:
        db.execute('UPDATE mcp_calls SET status=?,result=?,error=?,updated_at=? WHERE id=?',(status,encoded,error,server.now(),ident))
        server.log(db,call['task_id'],'mcp_call_finished',call['server_id']+' · '+call['tool']+' · '+status)
    return {'id':ident,'status':status,'result':json.loads(encoded) if encoded else None,'error':error}


def prepare(server,user,sid,name,arguments,task_id=None,auto=False,revision=None):
    if not isinstance(arguments,dict) or len(json.dumps(arguments))>64000:raise ValueError('MCP araç argümanları geçersiz')
    with server.connect() as db:
        if task_id:server.access.record(db,user,'tasks',task_id,True)
        row=db.execute('SELECT * FROM mcp_servers WHERE id=? AND enabled=1',(sid,)).fetchone()
        if not row or revision is not None and row['revision']!=revision:raise ValueError('MCP bağlantısı veya sürümü değişti')
        tool=next((t for t in json.loads(row['tools']) if t['name']==name),None)
        if not tool or tool['policy']=='blocked' or auto and tool['policy']!='task_read':raise PermissionError('MCP aracına izin verilmedi')
        try:Draft202012Validator(tool['inputSchema']).validate(arguments)
        except Exception:raise ValueError('MCP araç argümanları şemaya uymuyor') from None
        ident=uuid.uuid4().hex;status='approved' if auto else 'pending'
        db.execute('INSERT INTO mcp_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(ident,sid,name,user['id'],task_id,json.dumps(arguments),status,None,None,row['revision'],server.now(),server.now()))
        server.audit.emit(db,'mcp_prepared',task_id,ident)
    return ident


class Router:
    def __init__(self,server,task,web=None):
        self.server=server;self.task=task;self.web=web;self.mapping={};self.descriptors=[]
        with server.connect() as db:
            for selected in json.loads(task.get('mcp_selection','[]')):
                row=db.execute('SELECT * FROM mcp_servers WHERE id=? AND enabled=1',(selected['id'],)).fetchone()
                if not row or row['revision']!=selected['revision']:raise ValueError('MCP izni görevden sonra değişti')
                for tool in json.loads(row['tools']):
                    if tool['policy']!='task_read':continue
                    name='mcp_'+hashlib.sha256((row['id']+':'+tool['name']).encode()).hexdigest()[:24]
                    self.mapping[name]=(row['id'],tool['name'],row['revision'])
                    self.descriptors.append({'type':'function','function':{'name':name,'description':row['name']+' / '+tool['name']+': '+tool['description'],'parameters':tool['inputSchema']}})
    def call(self,name,args):
        if name in ('web_search','web_open') and self.web:return self.web.call(name,args)
        if name not in self.mapping:raise PermissionError('MCP aracı görevde seçili değil')
        sid,tool,revision=self.mapping[name]
        with self.server.connect() as db:user=dict(db.execute('SELECT * FROM users WHERE id=?',(self.task['owner_id'],)).fetchone())
        if user['role']=='viewer':raise PermissionError('Gözlemci araç çalıştıramaz')
        call=prepare(self.server,user,sid,tool,args,self.task['id'],True,revision)
        result=execute(self.server,call)
        return json.dumps(result['result'] or {'error':result['error']},ensure_ascii=False)
    def close(self):
        if self.web:self.web.close()


def handle_get(handler,user,path,db,server):
    if path=='/api/mcp/history':
        if user['role']!='admin':raise PermissionError('MCP yönetimi için yönetici gerekli')
        query=parse_qs(urlparse(handler.path).query)
        before=int(query['before'][0]) if 'before' in query else None
        handler.respond(200,mcp_versions.history(db,query.get('server_id',[''])[0],before));return True
    if path!='/api/mcp':return False
    admin=user['role']=='admin'
    rows=[public(r,admin) for r in db.execute('SELECT * FROM mcp_servers ORDER BY created_at')]
    calls=[dict(r) for r in db.execute('SELECT * FROM mcp_calls ORDER BY created_at DESC LIMIT 50')] if admin else []
    for row in calls:row['arguments']=json.loads(row['arguments']);row['result']=json.loads(row['result']) if row['result'] else None
    handler.respond(200,{'servers':rows,'calls':calls,'stdio_profiles':list(manifest()) if admin else [],'revision_history':admin});return True


def handle_post(handler,user,path,body,server):
    if not path.startswith('/api/mcp'):return False
    if user['role']!='admin':raise PermissionError('MCP yönetimi için yönetici gerekli')
    ident=body.get('id') or uuid.uuid4().hex
    if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{32}',ident):raise ValueError('MCP kimliği geçersiz')
    if path=='/api/mcp':
        name=body.get('name','');transport=body.get('transport','http')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100 or transport not in ('http','stdio'):raise ValueError('MCP bağlantı ayarları geçersiz')
        with server.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM mcp_servers WHERE id=?',(ident,)).fetchone();previous=json.loads(old['config']) if old else {}
            mcp_versions.expected(old,body)
            cfg={'transport':transport,'timeout':body.get('timeout',30)}
            if type(cfg['timeout']) is not int or not 1<=cfg['timeout']<=120:raise ValueError('MCP süre sınırı 1–120 saniye olmalı')
            if transport=='http':
                cfg.update(url=body.get('url'),allow_private=body.get('allow_private') is True)
                endpoint(cfg['url'],cfg['allow_private'])
                token=body.get('token','')
                if not isinstance(token,str) or len(token)>8000 or any(ord(c)<32 for c in token):raise ValueError('MCP anahtarı geçersiz')
                cfg['encrypted_token']=server.encrypt(server.DB_PATH,token) if token else previous.get('encrypted_token','') if not body.get('clear_token') else ''
            else:
                cfg['profile']=body.get('profile')
                if cfg['profile'] not in manifest():raise ValueError('MCP stdio profili yerel manifestte bulunmalı')
            revision=old['revision']+1 if old else 1
            db.execute('INSERT OR REPLACE INTO mcp_servers VALUES(?,?,?,?,?,?,?,?,?)',(ident,name.strip(),json.dumps(cfg),0,'[]',revision,None,None,server.now()))
            mcp_versions.record(db,ident,'connection',user['id'],server.now())
            server.log(db,None,'mcp_configured',ident)
        handler.respond(201,{'id':ident});return True
    with server.connect() as db:row=db.execute('SELECT * FROM mcp_servers WHERE id=?',(ident,)).fetchone()
    if path=='/api/mcp/approve':
        with server.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("UPDATE mcp_calls SET status='approved',updated_at=? WHERE id=? AND status='pending'",(server.now(),ident)).rowcount:raise ValueError('Bekleyen MCP çağrısı yok')
            server.audit.emit(db,'mcp_approved',target_id=ident)
        result=execute(server,ident);handler.respond(200,result);return True
    if path=='/api/mcp/reject':
        with server.connect() as db:
            if db.execute("UPDATE mcp_calls SET status='rejected',updated_at=? WHERE id=? AND status='pending'",(server.now(),ident)).rowcount:server.audit.emit(db,'mcp_rejected',target_id=ident)
        handler.respond(200,{'ok':True});return True
    if not row:raise ValueError('MCP sunucusu bulunamadı')
    mcp_versions.expected(row,body)
    if path=='/api/mcp/discover':
        client=None
        try:
            client=connection(server,row);tools=valid_tools(client.tools(),json.loads(row['tools']))
            with server.connect() as db:
                db.execute('BEGIN IMMEDIATE');mcp_versions.guard(db,row)
                db.execute("UPDATE mcp_servers SET tools=?,checked_at=?,error=NULL,revision=revision+1,enabled=0 WHERE id=?",(json.dumps(tools),server.now(),ident))
                mcp_versions.record(db,ident,'discovery',user['id'],server.now())
        except Exception:
            with server.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                changed=db.execute("UPDATE mcp_servers SET error=?,checked_at=?,enabled=0,revision=revision+1 WHERE id=? AND revision=?",('MCP araç keşfi başarısız; adresi, anahtarı ve protokolü kontrol et',server.now(),ident,row['revision'])).rowcount
                if changed:mcp_versions.record(db,ident,'discovery_failed',user['id'],server.now())
            raise ValueError('MCP araç keşfi başarısız; adresi, anahtarı ve protokolü kontrol et') from None
        finally:
            if client:client.close()
    elif path=='/api/mcp/policy':
        policies=body.get('policies',{})
        if not isinstance(policies,dict) or any(v not in ('blocked','manual','task_read') for v in policies.values()):raise ValueError('MCP izin politikası geçersiz')
        tools=json.loads(row['tools'])
        if any(k not in {t['name'] for t in tools} for k in policies):raise ValueError('MCP aracı bulunamadı')
        for tool in tools:tool['policy']=policies.get(tool['name'],tool['policy'])
        with server.connect() as db:
            db.execute('BEGIN IMMEDIATE');mcp_versions.guard(db,row)
            db.execute('UPDATE mcp_servers SET tools=?,enabled=?,revision=revision+1 WHERE id=?',(json.dumps(tools),int(body.get('enabled') is True),ident))
            mcp_versions.record(db,ident,'policy',user['id'],server.now())
    elif path=='/api/mcp/call':
        call=prepare(server,user,ident,body.get('tool'),body.get('arguments'),revision=row['revision'])
        handler.respond(201,{'id':call,'status':'pending'});return True
    elif path=='/api/mcp/disable':
        with server.connect() as db:
            db.execute('BEGIN IMMEDIATE');mcp_versions.guard(db,row)
            db.execute('UPDATE mcp_servers SET enabled=0,revision=revision+1 WHERE id=?',(ident,))
            mcp_versions.record(db,ident,'disabled',user['id'],server.now())
    else:return False
    with server.connect() as db:server.log(db,None,'mcp_updated',ident+' · '+path.rsplit('/',1)[-1])
    handler.respond(200,{'ok':True});return True
