"""Local context review and opt-in text redaction at every provider/tool boundary."""
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import parse_qs,urlparse

RULES=[
 ('TOKEN',re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b')),
 ('JWT',re.compile(r'(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,4096}\.[A-Za-z0-9_-]{8,4096}\.[A-Za-z0-9_-]{8,4096}(?![A-Za-z0-9_-])')),
 ('EMAIL',re.compile(r'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}(?![A-Za-z])')),
]
ASSIGN=re.compile(r'(?i)(["\']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)["\']?\s*[:=]\s*["\']?)(?:Bearer\s+)?([^\s"\'&,;<>]+)')
ASSIGN_QUOTED=re.compile(r"""(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)["']?\s*[:=]\s*)(?:"([^"\r\n]*)"|'([^'\r\n]*)')""")
SECRET_KEYS={'api_key','apikey','access_token','refresh_token','password','passwd','secret','authorization'}


def masked(value,seed,kind):
    if re.fullmatch(r'\[MASKED_[A-Z_]+_[a-f0-9]{12}\]',value):return value
    digest=hmac.new(seed.encode(),value.encode(),hashlib.sha256).hexdigest()[:12]
    return '[MASKED_'+kind+'_'+digest+']'


def redact(value,seed):
    # A single forward scan avoids quadratic unmatched-PEM backtracking.
    parts=[];offset=0;header=re.compile(r'-----BEGIN ((?:[A-Z]{1,16} )?PRIVATE KEY)-----')
    while True:
        match=header.search(value,offset)
        if not match:parts.append(value[offset:]);break
        parts.append(value[offset:match.start()]);marker='-----END '+match.group(1)+'-----'
        end=value.find(marker,match.end());end=len(value) if end<0 else end+len(marker)
        parts.append(masked(value[match.start():end],seed,'PRIVATE_KEY'));offset=end
    result=''.join(parts)
    for kind,pattern in RULES:result=pattern.sub(lambda m:masked(m.group(),seed,kind),result)
    result=ASSIGN_QUOTED.sub(lambda m:m.group(1)+('\"'+masked(m.group(2),seed,'SECRET')+'\"' if m.group(2) is not None else "'"+masked(m.group(3),seed,'SECRET')+"'"),result)
    result=ASSIGN.sub(lambda m:m.group(1)+masked(m.group(2),seed,'SECRET'),result)
    return result


def scrub(value,seed,key=''):
    if not seed:return value
    if isinstance(value,str):
        if key=='arguments':
            try:return json.dumps(scrub(json.loads(value),seed),ensure_ascii=False)
            except (ValueError,TypeError,RecursionError):pass
        if value.startswith('data:image/'):return value # Binary images require separate explicit consent.
        if key.lower() in SECRET_KEYS:return masked(value,seed,'SECRET')
        return redact(value,seed)
    if isinstance(value,list):return [scrub(v,seed) for v in value]
    if isinstance(value,dict):return {k:scrub(v,seed,k) for k,v in value.items()}
    return value


def initialize(db):
    for column in ('mask_sensitive INTEGER NOT NULL DEFAULT 0','allow_unmasked_images INTEGER NOT NULL DEFAULT 0',"privacy_seed TEXT NOT NULL DEFAULT ''"):
        if column.split()[0] not in {r[1] for r in db.execute('PRAGMA table_info(tasks)')}:db.execute('ALTER TABLE tasks ADD COLUMN '+column)
    db.execute('CREATE TABLE IF NOT EXISTS context_previews(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,config_hash TEXT NOT NULL,source_hash TEXT NOT NULL,seed TEXT NOT NULL,created_at REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0)')
    db.execute('DELETE FROM context_previews WHERE created_at<? OR used=1',(time.time()-1800,))


def config(body,server):
    cfg=server.workspace.clean_config(body,server)
    cfg['mask_sensitive']=body.get('mask_sensitive') is True
    cfg['allow_unmasked_images']=body.get('allow_unmasked_images') is True
    return cfg


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def sources(server,db,user,cfg):
    server.workspace.task_options(db,user,cfg,server)
    result=[{'id':'prompt','name':'Prompt','text':cfg['prompt'],'kind':'text'}]
    pid=cfg.get('project_id') or None
    project=db.execute('SELECT instruction FROM projects WHERE id=?',(pid,)).fetchone()
    if project:result.append({'id':'project','name':'Proje talimatı','text':project[0],'kind':'text'})
    ids=list(dict.fromkeys(cfg['agents']+([cfg.get('judge')] if cfg.get('mode')=='council' and cfg.get('judge') else [])+([cfg.get('fallback_agent')] if cfg.get('fallback_agent') else [])))
    for aid in ids:result.append({'id':'agent:'+aid,'name':server.BY_ID[aid]['name'],'text':server.BY_ID[aid]['instruction'],'kind':'text','endpoint':server.BY_ID[aid].get('endpoint',''),'model':server.BY_ID[aid]['model']})
    server.platform.knowledge_context(db,pid,cfg.get('knowledge_ids',[])) # validates explicit source scope
    for kid in cfg.get('knowledge_ids',[]):
        row=db.execute('SELECT title,content FROM knowledge WHERE id=?',(kid,)).fetchone()
        result.append({'id':'knowledge:'+kid,'name':row['title'],'text':row['content'],'kind':'text'})
    for fid in cfg.get('file_ids',[]):
        row=db.execute('SELECT name,kind,length(text) characters,sha256 FROM files WHERE id=?',(fid,)).fetchone()
        result.append({'id':'file:'+fid,'name':row['name'],'text':'', 'characters':row['characters'] or 0,'kind':row['kind'],'sha256':row['sha256']})
    return result


def verify(server,db,user,body):
    seed=secrets.token_hex(32) if body.get('mask_sensitive') is True else ''
    ident=body.get('context_preview_id')
    if not ident:return seed
    row=db.execute('SELECT * FROM context_previews WHERE id=? AND owner_id=?',(ident,user['id'])).fetchone()
    cfg=config(body,server)
    if not row or row['used'] or row['created_at']<time.time()-1800 or row['config_hash']!=digest(cfg) or row['source_hash']!=digest(sources(server,db,user,cfg)):
        raise ValueError('Bağlam değişti veya önizleme sona erdi; yeniden incele')
    db.execute('UPDATE context_previews SET used=1 WHERE id=?',(ident,))
    return row['seed'] if cfg['mask_sensitive'] else ''


def handle_post(handler,user,path,body,server):
    if path=='/api/context/page':
        if user['role']=='viewer':raise PermissionError('Gözlemci görev hazırlayamaz')
        page(handler,user,body,server);return True
    if path!='/api/context/preview':return False
    if user['role']=='viewer':raise PermissionError('Gözlemci görev hazırlayamaz')
    with server.CONFIG_LOCK,server.connect() as db:
        cfg=config(body,server);rows=sources(server,db,user,cfg)
        if cfg['mask_sensitive'] and any(s['kind']=='image' for s in rows) and not cfg['allow_unmasked_images']:raise ValueError('Görsel metni maskelenemez; görselleri çıkar veya maskelenmeden gönderilmesini açıkça onayla')
        ident=secrets.token_hex(16);seed=secrets.token_hex(32)
        db.execute('DELETE FROM context_previews WHERE created_at<? OR used=1',(time.time()-1800,))
        if db.execute('SELECT count(*) FROM context_previews WHERE owner_id=?',(user['id'],)).fetchone()[0]>=100:raise ValueError('Önizleme sayısı dolu; eski önizlemelerin sona ermesini bekle')
        db.execute('INSERT INTO context_previews VALUES(?,?,?,?,?,?,0)',(ident,user['id'],digest(cfg),digest(rows),seed,time.time()))
    meta=[{k:v for k,v in s.items() if k!='text'}|{'characters':s.get('characters',len(s['text']))} for s in rows]
    handler.respond(200,{'id':ident,'sources':meta,'mask_sensitive':cfg['mask_sensitive']});return True


def page(handler,user,body,server):
    with server.CONFIG_LOCK,server.connect() as db:
        row=db.execute('SELECT * FROM context_previews WHERE id=? AND owner_id=?',(body.get('id'),user['id'])).fetchone()
        cfg=config(body.get('config',{}),server)
        rows=sources(server,db,user,cfg)
        if not row or row['created_at']<time.time()-1800 or row['config_hash']!=digest(cfg) or row['source_hash']!=digest(rows):raise ValueError('Bağlam değişti veya önizleme sona erdi; yeniden incele')
        source=next((s for s in rows if s['id']==body.get('source_id')),None)
        offset=body.get('offset',0)
        if not source or type(offset) is not int or offset<0:raise ValueError('Önizleme sayfası geçersiz')
        raw=source['text']
        if source['id'].startswith('file:') and source['kind']=='text':raw=db.execute('SELECT text FROM files WHERE id=?',(source['id'][5:],)).fetchone()[0]
        content=redact(raw,row['seed']) if cfg['mask_sensitive'] else raw
        result={'text':content[offset:offset+16000],'characters':len(content),'offset':offset,'next_offset':offset+16000 if len(content)>offset+16000 else None,'changed':raw!=content}
    handler.respond(200,result)


def outbound(payload,seed):
    """Redact text, not routing IDs or provider-signed opaque protocol fields."""
    if not seed:return payload
    result=dict(payload)
    for key in ('prompt','context'):
        if key in result:result[key]=scrub(result[key],seed)
    if 'messages' in result:
        messages=[]
        for item in result['messages']:
            message=dict(item)
            for key in ('content','reasoning_content'):
                if key in message:message[key]=scrub(message[key],seed)
            if 'tool_calls' in message:
                calls=[]
                for item in message['tool_calls']:
                    call=dict(item);function=dict(call['function']);function['arguments']=scrub(function['arguments'],seed,'arguments');call['function']=function;calls.append(call)
                message['tool_calls']=calls
            messages.append(message)
        result['messages']=messages
    if 'tools' in result:
        tools=[]
        for item in result['tools']:
            tool=dict(item)
            if 'function' in tool:
                function=dict(tool['function'])
                for key in ('description','parameters'):
                    if key in function:function[key]=scrub(function[key],seed)
                tool['function']=function
            tools.append(tool)
        result['tools']=tools
    return result
