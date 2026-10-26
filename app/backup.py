"""Portable authenticated encrypted backups; restoration never overwrites live data."""
import argparse
from contextlib import closing
import base64
import getpass
import datetime as dt
import hashlib
import io
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import zipfile
from cryptography.fernet import Fernet, InvalidToken

MAGIC=b'ECYBAK1\n'
MAX_BYTES=1024*1024*1024


def cipher(password,salt):
    if not isinstance(password,str) or not 12<=len(password)<=256:
        raise ValueError('Yedek parolası 12–256 karakter olmalı')
    return Fernet(base64.urlsafe_b64encode(hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000)))


def readiness(db):
    from .operations import dispatch_paused
    counts={
        'queued':db.execute("SELECT count(*) FROM tasks WHERE status='queued'").fetchone()[0],
        'running_tasks':db.execute("SELECT count(*) FROM tasks WHERE status='running'").fetchone()[0],
        'running_runs':db.execute("SELECT count(*) FROM runs WHERE status='running'").fetchone()[0],
        'mcp_active':db.execute("SELECT count(*) FROM mcp_calls WHERE status IN ('running','dispatched')").fetchone()[0],
        'notifications_sending':db.execute("SELECT count(*) FROM notifications WHERE status='sending'").fetchone()[0]}
    paused=dispatch_paused(db)
    ready=not any(counts[k] for k in ('running_tasks','running_runs','mcp_active','notifications_sending')) and (not counts['queued'] or paused)
    return {'ready':ready,'dispatch_paused':paused,**counts}


def checksum(path):
    digest=hashlib.sha256()
    with open(path,'rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def create(server,password):
    salt=os.urandom(16);fernet=cipher(password,salt);root=Path(server.DB_PATH).parent
    with server.CONFIG_LOCK,closing(server.connect()) as lock,lock,tempfile.TemporaryDirectory() as temp:
        lock.execute('BEGIN IMMEDIATE')
        if not readiness(lock)['ready']:raise ValueError('Yedek için çalışan işleri bitir; kuyrukta görev varsa önce işlemeyi duraklat')
        snapshot=Path(temp)/'harbor.db'
        with closing(server.connect()) as reader,closing(sqlite3.connect(snapshot)) as dest:reader.backup(dest)
        buffer=io.BytesIO();inventory={}
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive,closing(sqlite3.connect(snapshot)) as db:
            def add(path,name):
                inventory[name]={'size':path.stat().st_size,'sha256':checksum(path)};archive.write(path,name)
            add(snapshot,'harbor.db');key=root/'master.key'
            from .vault import LOCK as key_lock
            with key_lock:
                if key.exists():add(key,'master.key')
            for fid,size,sha in db.execute('SELECT id,size,sha256 FROM files'):
                if not re.fullmatch(r'[a-f0-9]{32}',fid) or type(size) is not int or not 0<size<=MAX_BYTES:raise ValueError('Yedek kaynak kaydı geçersiz')
                path=root/'uploads'/fid
                if not path.is_file():raise ValueError('Dosya yazımı sürüyor; yedeklemeyi tekrar dene')
                with open(path,'rb') as source:raw=source.read(size+1)
                if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Kaynak dosyası boyutu veya özeti uyuşmuyor; yedeklemeyi tekrar dene')
                name='uploads/'+fid;inventory[name]={'size':size,'sha256':sha};archive.writestr(name,raw)
            for path in (root/'artifacts').glob('*.txt'):add(path,'artifacts/'+path.name)
            archive.writestr('manifest.json',json.dumps({'format':1,'created_at':server.now(),'timezone':'Europe/Istanbul','environment_keys_included':False,'integrity':{'algorithm':'sha256','members':inventory}}))
        raw=buffer.getvalue()
        if len(raw)>MAX_BYTES:raise ValueError('Yedek çok büyük; çevrimdışı dosya yedeği kullan')
        server.audit.emit(lock,'backup_created')
    return MAGIC+salt+fernet.encrypt(raw)


def unpack(content,password):
    if len(content)>MAX_BYTES*2 or not content.startswith(MAGIC):raise ValueError('Yedek biçimi geçersiz')
    salt=content[len(MAGIC):len(MAGIC)+16]
    try:raw=cipher(password,salt).decrypt(content[len(MAGIC)+16:])
    except InvalidToken:raise ValueError('Yedek parolası hatalı veya yedek bozulmuş') from None
    archive=zipfile.ZipFile(io.BytesIO(raw))
    entries=archive.infolist()
    if len(entries)>100000 or sum(e.file_size for e in entries)>MAX_BYTES*4:raise ValueError('Yedek boyutu geçersiz')
    names=[e.filename for e in entries]
    if len(names)!=len(set(names)):raise ValueError('Tekrarlanan yedek kaydı')
    for entry in entries:
        path=PurePosixPath(entry.filename)
        allowed=entry.filename in ('manifest.json','harbor.db','master.key') or len(path.parts)==2 and path.parts[0] in ('uploads','artifacts')
        if not allowed or path.is_absolute() or '..' in path.parts or '\\' in entry.filename or entry.is_dir() or ((entry.external_attr>>16)&0o170000)==0o120000:raise ValueError('Güvensiz yedek yolu')
    if 'harbor.db' not in names or 'manifest.json' not in names:raise ValueError('Yedek sürümü geçersiz')
    manifest=json.loads(archive.read('manifest.json'))
    if not isinstance(manifest,dict) or manifest.get('format')!=1:raise ValueError('Yedek sürümü geçersiz')
    inventory=manifest.get('integrity')
    if inventory is not None:
        if not isinstance(inventory,dict) or inventory.get('algorithm')!='sha256' or not isinstance(inventory.get('members'),dict) or set(inventory['members'])!=set(names)-{'manifest.json'}:raise ValueError('Yedek bütünlük envanteri geçersiz')
        for entry in entries:
            if entry.filename=='manifest.json':continue
            item=inventory['members'][entry.filename]
            if not isinstance(item,dict) or item.get('size')!=entry.file_size or not isinstance(item.get('sha256'),str) or not re.fullmatch(r'[a-f0-9]{64}',item['sha256']):raise ValueError('Yedek bütünlük envanteri geçersiz')
            digest=hashlib.sha256()
            with archive.open(entry) as source:
                for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
            if digest.hexdigest()!=item['sha256']:raise ValueError('Yedek dosya özeti doğrulanamadı')
    return archive


def restore(content,password,destination,*,return_report=False):
    requested=Path(destination).absolute()
    if requested.exists() or requested.is_symlink():raise ValueError('Geri yükleme için yeni, boş bir klasör adı kullan')
    target=requested.resolve()
    if target.exists():raise ValueError('Geri yükleme için yeni, boş bir klasör adı kullan')
    archive=unpack(content,password)
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staging=Path(temp)/'data';staging.mkdir(mode=0o700)
        for entry in archive.infolist():
            if entry.filename=='manifest.json':continue
            path=staging/entry.filename;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with open(path,'xb') as out:out.write(archive.read(entry))
            os.chmod(path,0o600)
        with closing(sqlite3.connect(staging/'harbor.db')) as db,db:
            db.row_factory=sqlite3.Row
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Veritabanı doğrulanamadı')
            report=validate_directory(staging,db)
            report['inventory_verified']='integrity' in json.loads(archive.read('manifest.json'))
            report['pending_tasks_held']=db.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running','awaiting_approval')").fetchone()[0]
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='context_previews'").fetchone():db.execute("DELETE FROM context_previews")
            for table in ('schedules','webhooks','mcp_servers','nodes'):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():db.execute('UPDATE '+table+' SET enabled=0')
            db.execute("UPDATE tasks SET status='incomplete',error='Yedekten geri yüklendi; otomatik başlatılmadı' WHERE status IN ('queued','running','awaiting_approval')")
            db.execute("UPDATE runs SET status='incomplete' WHERE status IN ('queued','running')")
            db.execute("UPDATE approvals SET status='rejected' WHERE status='pending'")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mcp_calls'").fetchone():db.execute("UPDATE mcp_calls SET status='rejected' WHERE status IN ('pending','approved','running','dispatched')")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='playbook_runs'").fetchone():db.execute("UPDATE playbook_runs SET control='paused' WHERE control='ready'")
            db.execute("UPDATE notifications SET status='unknown' WHERE status IN ('pending','sending')")
            row=db.execute("SELECT config FROM integrations WHERE id='telegram'").fetchone()
            if row:
                cfg=json.loads(row[0]);cfg['enabled']=False;db.execute("UPDATE integrations SET config=? WHERE id='telegram'",(json.dumps(cfg),))
            for fid, in db.execute('SELECT id FROM files'):
                if not re.fullmatch(r'[a-f0-9]{32}',fid):raise ValueError('Geçersiz dosya kimliği')
                if not (staging/'uploads'/fid).is_file():raise ValueError('Yedekte kaynak dosyası eksik')
            from . import operations,audit
            operations.initialize_dispatch(db)
            db.execute("UPDATE dispatch_control SET paused=1,changed_by='system',changed_at=? WHERE id=1",(dt.datetime.now(dt.timezone.utc).isoformat(),))
            audit.initialize(db);audit.emit(db,'backup_restored')
            final=validate_directory(staging,db)
            ensure_suspended(db)
            report['restored_audit_events_verified']=final['audit_events_verified']
        # Exclusive mkdir prevents a concurrent existing destination from being replaced.
        try:target.mkdir(mode=0o700)
        except FileExistsError:raise ValueError('Geri yükleme hedefi zaten var; üzerine yazılmadı') from None
        for child in staging.iterdir():os.rename(child,target/child.name)
    return (target,report) if return_report else target


def validate_directory(root,db):
    if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Yedek veritabanı ilişkileri doğrulanamadı')
    report={'files_verified':0,'encrypted_values_verified':0,'audit_present':False,'audit_events_verified':0}
    for row in db.execute('SELECT id,size,sha256 FROM files'):
        fid=row['id']
        if not re.fullmatch(r'[a-f0-9]{32}',fid):raise ValueError('Geçersiz dosya kimliği')
        path=root/'uploads'/fid
        if not path.is_file() or path.stat().st_size!=row['size'] or checksum(path)!=row['sha256']:raise ValueError('Yedekte kaynak dosyası eksik veya özeti uyuşmuyor')
        report['files_verified']+=1
    encrypted=[]
    for row in db.execute('SELECT config FROM agent_configs'):
        value=json.loads(row[0]).get('encrypted_key')
        if value:encrypted.append(value)
    for table,key in (('mcp_servers','encrypted_token'),('integrations','token')):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            for row in db.execute('SELECT config FROM '+table):
                value=json.loads(row[0]).get(key)
                if value:encrypted.append(value)
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='security_mfa'").fetchone():
        for row in db.execute('SELECT secret,pending FROM security_mfa'):encrypted.extend(v for v in row if v)
    key=root/'master.key';report['master_key_present']=key.is_file()
    try:
        vault=Fernet(key.read_bytes()) if key.is_file() else None
        if encrypted and vault is None:raise ValueError('missing key')
        for value in encrypted:vault.decrypt(value.encode());report['encrypted_values_verified']+=1
    except (ValueError,TypeError,AttributeError,InvalidToken):raise ValueError('Yedekteki kayıtlı anahtarlar doğrulanamadı') from None
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_events'").fetchone():
        from . import audit
        report['audit_present']=True;head=audit.checkpoint(db);after=0
        while True:
            checked=audit.verify_chunk(db,after,head['seq'])
            if not checked['valid']:raise ValueError('Yedekteki denetim zinciri doğrulanamadı')
            report['audit_events_verified']+=checked['checked']
            if checked['next_after'] is None:break
            after=checked['next_after']
    return report


def ensure_suspended(db):
    from .operations import dispatch_paused
    if not dispatch_paused(db):raise ValueError('Geri yüklenen görev işleme durumu doğrulanamadı')
    for table in ('schedules','webhooks','mcp_servers','nodes'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() and db.execute('SELECT 1 FROM '+table+' WHERE enabled!=0 LIMIT 1').fetchone():raise ValueError('Geri yüklenen otomasyonlar kapatılamadı')
    for table,column,states in (('tasks','status',('queued','running','awaiting_approval')),('runs','status',('queued','running')),('approvals','status',('pending',)),('mcp_calls','status',('pending','approved','running','dispatched')),('notifications','status',('pending','sending')),('playbook_runs','control',('ready',))):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() and db.execute('SELECT 1 FROM '+table+' WHERE '+column+' IN ('+','.join('?' for _ in states)+') LIMIT 1',states).fetchone():raise ValueError('Geri yüklenen bekleyen işler askıya alınamadı')
    row=db.execute("SELECT config FROM integrations WHERE id='telegram'").fetchone()
    if row and json.loads(row[0]).get('enabled'):raise ValueError('Geri yüklenen bildirimler kapatılamadı')


def verify(content,password):
    # Restore only inside a disposable directory. No server/worker is imported or started.
    with tempfile.TemporaryDirectory(prefix='harbor-restore-drill-') as folder:
        _,report=restore(content,password,Path(folder)/'restored',return_report=True)
    return {'verified':True,'network_calls':False,'live_data_modified':False,'dispatch_paused':True,'automation_disabled':True,**report}


def main():
    parser=argparse.ArgumentParser(description='Verify or restore ECY encrypted backups without overwriting existing data.')
    parser.add_argument('command',choices=['restore','verify']);parser.add_argument('archive');parser.add_argument('--destination')
    args=parser.parse_args()
    if args.command=='restore' and not args.destination:parser.error('restore requires --destination')
    if args.command=='verify' and args.destination:parser.error('verify does not use --destination')
    password=getpass.getpass('Backup password: ')
    try:
        with open(args.archive,'rb') as source:content=source.read(MAX_BYTES*2+1)
        result=restore(content,password,args.destination) if args.command=='restore' else json.dumps(verify(content,password),indent=2)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,zipfile.BadZipFile):raise SystemExit('Yedek doğrulanamadı; parola, dosya bütünlüğü ve hedef klasörü kontrol et / Backup validation failed') from None
    print(result)

if __name__=='__main__':main()
