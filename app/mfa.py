"""TOTP second factor with one-time recovery codes and replay protection."""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS security_mfa(user_id TEXT PRIMARY KEY,secret TEXT,enabled INTEGER NOT NULL DEFAULT 0,pending TEXT,expires REAL,last_counter INTEGER NOT NULL DEFAULT -1,recovery TEXT NOT NULL DEFAULT \'[]\')')


def code(secret,counter):
    digest=hmac.new(base64.b32decode(secret),struct.pack('>Q',counter),hashlib.sha1).digest();index=digest[-1]&15
    return str((int.from_bytes(digest[index:index+4],'big')&0x7fffffff)%1000000).zfill(6)


def counter_for(secret,value,stamp=None):
    if not isinstance(value,str) or len(value)!=6 or not value.isascii() or not value.isdigit():return None
    current=int((time.time() if stamp is None else stamp)//30)
    for counter in (current-1,current,current+1):
        if counter>=0 and hmac.compare_digest(code(secret,counter),value):return counter
    return None


def verify_login(server,uid,value):
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM security_mfa WHERE user_id=? AND enabled=1',(uid,)).fetchone()
        if not row:
            from .security import public_mode
            return not public_mode()
        secret=server.decrypt(server.DB_PATH,row['secret']);counter=counter_for(secret,value)
        if counter is not None and counter>row['last_counter']:
            db.execute('UPDATE security_mfa SET last_counter=? WHERE user_id=?',(counter,uid));return True
        recovery=json.loads(row['recovery']);digest=hashlib.sha256(str(value).encode()).hexdigest()
        if digest in recovery:
            recovery.remove(digest);db.execute('UPDATE security_mfa SET recovery=? WHERE user_id=?',(json.dumps(recovery),uid));return True
        return False


def statuses(db):return {r['user_id']:bool(r['enabled']) for r in db.execute('SELECT user_id,enabled FROM security_mfa')}


def handle_post(handler,user,path,body,server):
    if not path.startswith('/api/security/mfa/'):return False
    if user['role']!='admin' or not server.check_login('admin',body.get('password')):
        raise PermissionError('İşlem için yönetici parolanı doğrula')
    uid=body.get('user','admin')
    if uid not in ('admin','operator','viewer'):raise ValueError('Geçersiz kullanıcı')
    current_session=handler.user()[1]
    with server.connect() as check:
        protected=check.execute("SELECT 1 FROM security_mfa WHERE user_id='admin' AND enabled=1").fetchone() is not None
    if protected and current_session.get('mfa_reauth_until',0)<time.time():
        if not verify_login(server,'admin',body.get('admin_code','')):raise PermissionError('Yönetici ikinci adım kodunu doğrula')
        current_session['mfa_reauth_until']=time.time()+300
    result={'ok':True}
    with server.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM security_mfa WHERE user_id=?',(uid,)).fetchone()
        if path.endswith('/begin'):
            secret=base64.b32encode(secrets.token_bytes(20)).decode()
            db.execute('INSERT INTO security_mfa(user_id,pending,expires) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET pending=excluded.pending,expires=excluded.expires',(uid,server.encrypt(server.DB_PATH,secret),time.time()+600))
            result={'secret':secret,'uri':'otpauth://totp/'+quote('ECY Agent Harbor:'+uid)+'?secret='+secret+'&issuer=ECY%20Agent%20Harbor&digits=6&period=30'}
        elif path.endswith('/confirm'):
            if not row or not row['pending'] or row['expires']<time.time():raise ValueError('İki adımlı kurulum süresi doldu')
            secret=server.decrypt(server.DB_PATH,row['pending']);counter=counter_for(secret,body.get('code'))
            if counter is None:raise ValueError('Doğrulama kodu geçersiz')
            codes=[secrets.token_hex(6) for _ in range(10)]
            db.execute("UPDATE security_mfa SET secret=pending,enabled=1,pending=NULL,expires=NULL,last_counter=?,recovery=? WHERE user_id=?",(counter,json.dumps([hashlib.sha256(c.encode()).hexdigest() for c in codes]),uid))
            result={'recovery_codes':codes}
        elif path.endswith('/disable'):
            from .security import public_mode
            if public_mode():raise PermissionError('Dış erişim modunda iki adımlı doğrulama kapatılamaz')
            # Current second-factor proof must not use a second connection under this write lock.
            if not row or not row['enabled']:raise ValueError('İki adımlı doğrulama etkin değil')
            counter=counter_for(server.decrypt(server.DB_PATH,row['secret']),body.get('code'))
            if counter is None or counter<=row['last_counter']:raise ValueError('Doğrulama kodu geçersiz')
            db.execute('DELETE FROM security_mfa WHERE user_id=?',(uid,))
        else:return False
        server.log(db,None,'mfa_updated',uid+' · '+path.rsplit('/',1)[-1])
    if path.endswith(('/confirm','/disable')):
        with server.SESSION_LOCK:
            for key in [k for k,v in server.SESSIONS.items() if v['uid']==uid and v is not current_session]:server.SESSIONS.pop(key,None)
    handler.respond(200,result);return True
