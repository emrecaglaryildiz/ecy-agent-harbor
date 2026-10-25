"""Bounded MCP 2025 Streamable HTTP / trusted local stdio clients.

No shell, server-initiated sampling, roots, elicitation or automatic OAuth redirects.
"""
import http.client
import ipaddress
import json
import os
from pathlib import Path
import queue
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse

FRAME_BYTES=8*1024*1024
VERSIONS=('2025-11-25','2025-06-18','2025-03-26','2024-11-05')


def endpoint(value,allow_private=False):
    if not isinstance(value,str) or len(value)>2000:raise ValueError('MCP adresi geçersiz')
    parsed=urlparse(value)
    if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('MCP adresi HTTPS olmalı; anahtarı URL içine yazma')
    port=parsed.port or (443 if parsed.scheme=='https' else 80)
    addresses=socket.getaddrinfo(parsed.hostname,port,type=socket.SOCK_STREAM)
    if not addresses:raise ValueError('MCP adresi çözülemedi')
    for item in addresses:
        ip=ipaddress.ip_address(item[4][0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or (ip.is_reserved and not ip.is_loopback) or (not ip.is_global and not allow_private):
            raise ValueError('MCP ağ adresine izin verilmedi')
        if parsed.scheme=='http' and not ip.is_loopback:raise ValueError('HTTP yalnızca açıkça izin verilen yerel MCP için kullanılabilir')
    return parsed,addresses


def manifest():
    path=os.getenv('MCP_STDIO_MANIFEST','')
    if not path:return {}
    file=Path(path).resolve()
    if not file.is_file() or file.stat().st_mode&0o022:raise ValueError('MCP stdio manifesti grup veya herkes tarafından yazılabilir olmamalı')
    profiles=json.loads(file.read_text())
    if not isinstance(profiles,dict):raise ValueError('MCP manifesti geçersiz')
    return profiles


class Client:
    def __init__(self,config,token=''):
        self.config=config;self.token=token;self.sequence=0;self.session=None;self.version='2025-11-25';self.closed=False
        self.timeout=config.get('timeout',30)
        try:
            if config['transport']=='stdio':self._stdio()
            result=self.rpc('initialize',{'protocolVersion':self.version,'capabilities':{},'clientInfo':{'name':'ECY Agent Harbor','version':'0.3'}})
            self.version=result.get('protocolVersion',self.version)
            if self.version not in VERSIONS:raise ValueError('MCP protokol sürümü desteklenmiyor')
            self.rpc('notifications/initialized',{},notification=True)
        except BaseException:self.close();raise

    def _stdio(self):
        profile=manifest().get(self.config.get('profile'))
        if not isinstance(profile,dict):raise ValueError('Yerel MCP profili bulunamadı')
        command=profile.get('command');args=profile.get('args',[]);keys=profile.get('env_keys',[])
        if not isinstance(command,str) or not Path(command).is_absolute() or not Path(command).is_file() or not isinstance(args,list) or any(not isinstance(a,str) or '\x00' in a for a in args) or not isinstance(keys,list) or any(not isinstance(k,str) for k in keys):raise ValueError('MCP stdio profili geçersiz')
        self.directory=tempfile.TemporaryDirectory(prefix='harbor-mcp-')
        env={k:os.environ[k] for k in ('PATH','LANG','SYSTEMROOT') if k in os.environ}
        env.update(HOME=self.directory.name,TMPDIR=self.directory.name)
        for key in keys:
            if key in os.environ:env[key]=os.environ[key]
        self.process=subprocess.Popen([command]+args,cwd=self.directory.name,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
        self.responses=queue.Queue(maxsize=64)
        def read():
            try:
                while True:
                    line=self.process.stdout.readline(FRAME_BYTES+1)
                    if not line:break
                    if len(line)>FRAME_BYTES:raise ValueError('MCP mesajı çok büyük')
                    self.responses.put(json.loads(line),timeout=1)
            except Exception:pass
            finally:
                try:self.responses.put_nowait(None)
                except queue.Full:pass
        threading.Thread(target=read,daemon=True).start()

    def _http(self,message):
        parsed,addresses=endpoint(self.config['url'],self.config.get('allow_private',False))
        target=addresses[0][4]
        connection=(http.client.HTTPSConnection if parsed.scheme=='https' else http.client.HTTPConnection)(parsed.hostname,parsed.port,timeout=self.timeout)
        # Preserve TLS hostname verification/SNI while connecting to the validated IP.
        connection._create_connection=lambda address,timeout=30,source_address=None:socket.create_connection(target[:2],timeout,source_address)
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':self.version}
        if self.token:headers['Authorization']='Bearer '+self.token
        if self.session:headers['Mcp-Session-Id']=self.session
        socket_ref=[None]
        def abort():
            try:
                if socket_ref[0]:socket_ref[0].shutdown(socket.SHUT_RDWR)
            except OSError:pass
            connection.close()
        timer=threading.Timer(self.timeout,abort);timer.daemon=True;timer.start()
        try:
            connection.request('POST',parsed.path or '/',json.dumps(message).encode(),headers)
            socket_ref[0]=connection.sock
            response=connection.getresponse()
            if not 200<=response.status<300:raise ValueError('MCP HTTP isteği reddedildi')
            session=response.getheader('Mcp-Session-Id')
            if session:
                if len(session)>512 or any(not 33<=ord(c)<=126 for c in session):raise ValueError('MCP oturum başlığı geçersiz')
                self.session=session
            if response.status in (202,204):return None
            if 'text/event-stream' in response.getheader('Content-Type',''):
                event=[];size=0
                while True:
                    line=response.readline(FRAME_BYTES+1);size+=len(line)
                    if size>FRAME_BYTES:raise ValueError('MCP yanıtı çok büyük')
                    if not line:break
                    line=line.decode('utf-8').rstrip('\r\n')
                    if line.startswith('data:'):event.append(line[5:].lstrip())
                    elif not line and event:
                        item=json.loads('\n'.join(event));event=[]
                        if item.get('id')==message.get('id'):return item
                        if 'method' in item and 'id' in item:raise ValueError('MCP sunucusunun istemci işlemi desteklenmiyor')
                raise ValueError('MCP akışı sonuç üretmedi')
            raw=response.read(FRAME_BYTES+1)
            if len(raw)>FRAME_BYTES:raise ValueError('MCP yanıtı çok büyük')
            return json.loads(raw) if raw else None
        finally:timer.cancel();connection.close()

    def _send(self,message,deadline):
        done=threading.Event();errors=[]
        def write():
            try:self.process.stdin.write(json.dumps(message).encode()+b'\n');self.process.stdin.flush()
            except BaseException as exc:errors.append(exc)
            finally:done.set()
        thread=threading.Thread(target=write,daemon=True);thread.start()
        if not done.wait(max(0,deadline-time.monotonic())):
            self.close();thread.join(timeout=1);raise TimeoutError('MCP yazma süresi doldu')
        if errors:raise ValueError('MCP giriş bağlantısı kapandı')

    def rpc(self,method,params,notification=False):
        self.sequence+=1;ident=self.sequence
        message={'jsonrpc':'2.0','method':method,'params':params}
        if not notification:message['id']=ident
        if self.config['transport']=='stdio':
            deadline=time.monotonic()+self.timeout
            self._send(message,deadline)
            if notification:return {}
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    self.close();raise TimeoutError('MCP yanıt süresi doldu')
                try:result=self.responses.get(timeout=remaining)
                except queue.Empty:
                    self.close();raise TimeoutError('MCP yanıt süresi doldu') from None
                if result is None:raise ValueError('MCP bağlantısı kapandı')
                if 'method' in result and 'id' in result:
                    self._send({'jsonrpc':'2.0','id':result['id'],'error':{'code':-32601,'message':'Client requests are not supported'}},deadline);continue
                if result.get('id')==ident:break
        else:
            result=self._http(message)
            if notification:return {}
        if not isinstance(result,dict) or result.get('id')!=ident or result.get('jsonrpc')!='2.0' or 'error' in result:raise ValueError('MCP protokol yanıtı geçersiz')
        if not isinstance(result.get('result'),dict):raise ValueError('MCP sonuç nesnesi geçersiz')
        return result['result']

    def tools(self):
        tools=[];cursor=None;seen=set()
        for _ in range(20):
            result=self.rpc('tools/list',{'cursor':cursor} if cursor else {})
            page=result.get('tools',[])
            if not isinstance(page,list):raise ValueError('MCP araç listesi geçersiz')
            tools.extend(page)
            if len(tools)>200:raise ValueError('MCP araç sayısı güvenli sınırı aştı')
            cursor=result.get('nextCursor')
            if not cursor:return tools
            if cursor in seen:raise ValueError('MCP araç sayfalaması tekrarlıyor')
            seen.add(cursor)
        raise ValueError('MCP araç listesi tamamlanamadı')

    def call(self,name,arguments):return self.rpc('tools/call',{'name':name,'arguments':arguments})

    def close(self):
        if self.closed:return
        self.closed=True
        process=getattr(self,'process',None)
        if process:
            try:
                import signal
                if os.name!='nt':os.killpg(process.pid,signal.SIGTERM)
                else:process.terminate()
                process.wait(timeout=3)
            except (OSError,subprocess.TimeoutExpired):
                try:
                    if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
                    else:process.kill()
                except OSError:pass
                process.wait(timeout=3)
            for stream in (process.stdin,process.stdout):
                if stream:stream.close()
        if hasattr(self,'directory'):self.directory.cleanup()
