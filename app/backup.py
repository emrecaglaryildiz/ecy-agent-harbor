"""Portable authenticated encrypted backups; restoration never overwrites live data."""
import argparse
import base64
import getpass
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


def create(server,password):
    salt=os.urandom(16);fernet=cipher(password,salt)
    root=Path(server.DB_PATH).parent
    with server.CONFIG_LOCK, server.connect() as lock, tempfile.TemporaryDirectory() as temp:
        # Prevent metadata/file deletion during the consistent SQLite snapshot and file copy.
        lock.execute('BEGIN IMMEDIATE')
        if lock.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError('Yedek almadan önce çalışan görevlerin bitmesini bekle')
        snapshot=Path(temp)/'harbor.db'
        with server.connect() as reader, sqlite3.connect(snapshot) as dest:
            reader.backup(dest)
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive, sqlite3.connect(snapshot) as db:
            archive.writestr('manifest.json',json.dumps({'format':1,'created_at':server.now(),'timezone':'Europe/Istanbul','environment_keys_included':False}))
            archive.write(snapshot,'harbor.db')
            key=root/'master.key'
            if key.exists():archive.write(key,'master.key')
            for fid, in db.execute('SELECT id FROM files'):
                path=root/'uploads'/fid
                if not path.is_file():raise ValueError('Dosya yazımı sürüyor; yedeklemeyi tekrar dene')
                archive.write(path,'uploads/'+fid)
            for path in (root/'artifacts').glob('*.txt'):
                archive.write(path,'artifacts/'+path.name)
        raw=buffer.getvalue()
        if len(raw)>MAX_BYTES:raise ValueError('Yedek çok büyük; çevrimdışı dosya yedeği kullan')
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
    if 'harbor.db' not in names or json.loads(archive.read('manifest.json')).get('format')!=1:raise ValueError('Yedek sürümü geçersiz')
    return archive


def restore(content,password,destination):
    target=Path(destination).resolve()
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
        with sqlite3.connect(staging/'harbor.db') as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Veritabanı doğrulanamadı')
            for table in ('schedules','webhooks'):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():db.execute('UPDATE '+table+' SET enabled=0')
            db.execute("UPDATE tasks SET status='incomplete',error='Yedekten geri yüklendi; otomatik başlatılmadı' WHERE status IN ('queued','running','awaiting_approval')")
            db.execute("UPDATE runs SET status='incomplete' WHERE status IN ('queued','running')")
            db.execute("UPDATE approvals SET status='rejected' WHERE status='pending'")
            db.execute("UPDATE notifications SET status='unknown' WHERE status IN ('pending','sending')")
            row=db.execute("SELECT config FROM integrations WHERE id='telegram'").fetchone()
            if row:
                cfg=json.loads(row[0]);cfg['enabled']=False;db.execute("UPDATE integrations SET config=? WHERE id='telegram'",(json.dumps(cfg),))
            for fid, in db.execute('SELECT id FROM files'):
                if not re.fullmatch(r'[a-f0-9]{32}',fid):raise ValueError('Geçersiz dosya kimliği')
                if not (staging/'uploads'/fid).is_file():raise ValueError('Yedekte kaynak dosyası eksik')
        os.rename(staging,target)
    return target


def main():
    parser=argparse.ArgumentParser(description='Restore ECY encrypted backup into a NEW data directory; existing data is never overwritten.')
    parser.add_argument('command',choices=['restore']);parser.add_argument('archive');parser.add_argument('--destination',required=True)
    args=parser.parse_args()
    password=getpass.getpass('Backup password: ')
    print(restore(Path(args.archive).read_bytes(),password,args.destination))

if __name__=='__main__':main()
