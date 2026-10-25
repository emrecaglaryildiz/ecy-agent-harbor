"""Shared ingress, bootstrap and public-deployment controls."""
import collections
import hmac
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

LOCK=threading.RLock()
ATTEMPTS={}
KDF_SLOTS=threading.BoundedSemaphore(2)


def public_mode():return os.getenv('EXPOSURE_MODE','local')=='public'


def loopback(address):
    try:return ipaddress.ip_address(address.split('%')[0]).is_loopback
    except ValueError:return address.lower()=='localhost'


def rate_limit(key,limit=60,window=60):
    stamp=time.monotonic()
    with LOCK:
        if len(ATTEMPTS)>10000:
            for name,values in list(ATTEMPTS.items()):
                if not values or values[-1]<stamp-600:ATTEMPTS.pop(name,None)
            if len(ATTEMPTS)>10000:raise PermissionError('Çok fazla istek; daha sonra tekrar dene')
        values=ATTEMPTS.setdefault(key,collections.deque())
        while values and values[0]<=stamp-window:values.popleft()
        if len(values)>=limit:raise PermissionError('Çok fazla istek; daha sonra tekrar dene')
        values.append(stamp)


def bootstrap_token(server):
    configured=os.getenv('BOOTSTRAP_TOKEN','')
    if configured:
        if len(configured)<24:raise ValueError('BOOTSTRAP_TOKEN en az 24 karakter olmalı')
        return configured
    path=Path(server.DB_PATH).parent/'bootstrap.token'
    with LOCK:
        if not path.exists():
            path.parent.mkdir(parents=True,exist_ok=True)
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as out:out.write(secrets.token_urlsafe(32))
        return path.read_text().strip()


def authorize_bootstrap(server,body):
    if public_mode() or not server.requires_setup():raise PermissionError('İlk kurulum kapalı; yerel kurulumunu tamamla')
    value=body.get('bootstrap_token','')
    if not isinstance(value,str) or not hmac.compare_digest(value,bootstrap_token(server)):
        raise PermissionError('Kurulum kodu geçersiz; yerel terminaldeki kodu kullan')


def allowed_host(handler):
    value=handler.headers.get('Host','')
    if len(handler.headers.get_all('Host',[]))!=1:raise PermissionError('Host başlığı geçersiz')
    try:host=urlparse('http://'+value).hostname
    except ValueError:raise PermissionError('Host başlığı geçersiz') from None
    configured=os.getenv('PUBLIC_ORIGIN','')
    expected=urlparse(configured).hostname if configured else None
    if public_mode():
        if not expected or host!=expected:raise PermissionError('Host başlığı kabul edilmiyor')
    elif not (host and (loopback(host) or host==expected or host in os.getenv('ALLOWED_HOSTS','').split(','))):
        raise PermissionError('Host başlığı kabul edilmiyor')


def validate_public(server):
    if os.getenv('EXPOSURE_MODE','local') not in ('local','public'):raise ValueError('EXPOSURE_MODE local veya public olmalı')
    if not public_mode():return
    parsed=urlparse(os.getenv('PUBLIC_ORIGIN',''))
    if server.MODE!='live' or server.requires_setup() or parsed.scheme!='https' or not parsed.hostname or loopback(parsed.hostname) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or os.getenv('COOKIE_SECURE')!='1':
        raise ValueError('Dış erişim için live mod, tamamlanmış kurulum, HTTPS PUBLIC_ORIGIN ve COOKIE_SECURE=1 gerekli')
    from .mfa import statuses
    with server.connect() as db:
        enabled=statuses(db)
        accounts={r[0] for r in db.execute('SELECT user_id FROM credentials')}
    accounts|={uid for uid in ('admin','operator','viewer') if os.getenv('AUTH_'+uid.upper()+'_TOKEN')}
    if any(not enabled.get(uid,False) for uid in accounts):
        raise ValueError('Dış erişimden önce etkin hesaplarda iki adımlı doğrulamayı kur')
    if not sys.platform.startswith('linux') and os.getenv('PDF_UPLOADS')!='disabled':
        raise ValueError('Dış erişimde PDF için Linux kullan veya PDF_UPLOADS=disabled seç')



class BoundedHTTPServer(ThreadingHTTPServer):
    """Admission happens before spawning a handler, including slow-header clients."""
    daemon_threads=True
    request_queue_size=64
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(int(os.getenv('HTTP_MAX_CONNECTIONS','32')))
        super().__init__(*args,**kwargs)
    def get_request(self):
        connection,address=super().get_request();connection.settimeout(float(os.getenv('HTTP_READ_TIMEOUT','15')));return connection,address
    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request);return
        try:super().process_request(request,client_address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self.slots.release()


def read_json_body(handler,max_bytes):
    lengths=handler.headers.get_all('Content-Length',[])
    if len(lengths)!=1 or handler.headers.get('Transfer-Encoding'):
        raise ValueError('İstek gövdesi başlıkları geçersiz')
    length=int(lengths[0])
    if length<=0 or length>max_bytes:raise ValueError('İstek boyutu geçersiz')
    # An absolute deadline defeats clients which trickle bytes just before idle timeouts.
    timeout=float(os.getenv('HTTP_BODY_DEADLINE','30'))
    expired=threading.Event()
    def abort():
        expired.set()
        try:handler.connection.shutdown(socket.SHUT_RD)
        except OSError:pass
    timer=threading.Timer(timeout,abort);timer.daemon=True;timer.start()
    try:
        raw=handler.rfile.read(length)
        if len(raw)!=length or expired.is_set():raise ValueError('İstek gövdesi eksik veya süresi doldu')
        import json
        return json.loads(raw)
    finally:timer.cancel()


def posture(server):
    return {'exposure':os.getenv('EXPOSURE_MODE','local'),'bootstrap_complete':not server.requires_setup(),
            'secure_cookie':os.getenv('COOKIE_SECURE')=='1','https_origin':os.getenv('PUBLIC_ORIGIN','').startswith('https://'),
            'mode_live':server.MODE=='live','http_connection_limit':int(os.getenv('HTTP_MAX_CONNECTIONS','32')),
            'pdf_isolated':True,'pdf_hard_memory_limit':sys.platform.startswith('linux'),'pdf_enabled':os.getenv('PDF_UPLOADS')!='disabled','external_images_automatic':False,
            'notes':['MCP süreçleri işletim sistemi düzeyinde otomatik bir sandbox değildir.','TLS, proxy ve ağ korumaları gerçek yayın ortamında ayrıca doğrulanmalıdır.']}
