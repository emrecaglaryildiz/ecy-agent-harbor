"""Opt-in Docker/verified local TLS smoke test. Creates only disposable named resources.

No real API keys, domains or existing data are used. Requires a running Docker daemon.
Run explicitly: python3 tests/container_smoke.py (not part of unittest discovery).
"""
import base64
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import struct
import subprocess
import tempfile
import time


def docker(*args,env=None,check=True):
    return subprocess.run(['docker',*args],env=env,capture_output=True,text=True,check=check)


class Client:
    def __init__(self,port,context=None):self.port=port;self.context=context;self.cookie='';self.csrf=''
    def call(self,path,body=None,expected=200,csrf=True):
        connection=(http.client.HTTPSConnection('harbor.test',self.port,context=self.context,timeout=15) if self.context else http.client.HTTPConnection('127.0.0.1',self.port,timeout=15))
        if self.context:connection._create_connection=lambda address,timeout=15,source_address=None:socket.create_connection(('127.0.0.1',self.port),timeout)
        headers={'Content-Type':'application/json'}
        if self.cookie:headers['Cookie']=self.cookie
        if csrf and self.csrf:headers['X-CSRF-Token']=self.csrf
        try:
            connection.request('POST' if body is not None else 'GET',path,json.dumps(body).encode() if body is not None else None,headers)
            response=connection.getresponse();raw=response.read();status=response.status;result_headers=dict(response.getheaders())
            if response.getheader('Set-Cookie'):self.cookie=response.getheader('Set-Cookie').split(';',1)[0]
            assert status==expected,f'{path}: expected {expected}, got {status}'
            return json.loads(raw) if raw and response.getheader('Content-Type','').startswith('application/json') else raw,result_headers
        finally:connection.close()


def wait_health(client):
    for _ in range(60):
        try:
            data,_=client.call('/api/health')
            if data.get('ok'):return
        except (OSError,AssertionError,ValueError,http.client.HTTPException):time.sleep(.5)
    raise RuntimeError('Disposable service did not become healthy')


def totp(secret):
    value=hmac.new(base64.b32decode(secret),struct.pack('>Q',int(time.time()//30)),hashlib.sha1).digest();offset=value[-1]&15
    return str((int.from_bytes(value[offset:offset+4],'big')&0x7fffffff)%1000000).zfill(6)


def pdf():
    stream=b'BT /F1 12 Tf 40 100 Td (Local smoke evidence) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets[1:]);data+=b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n';return data


def main():
    docker('info','--format','{{.ServerVersion}}') # Fail before creating resources if unavailable.
    prefix='harbor-smoke-'+secrets.token_hex(6);image=prefix+':test';backend=prefix+'-app';gateway=prefix+'-tls';network=prefix+'-net';data=prefix+'-data';tls=prefix+'-tls-data'
    containers=[backend,gateway];volumes=[data,tls];env=dict(os.environ,BOOTSTRAP_TOKEN=secrets.token_urlsafe(32));password=secrets.token_urlsafe(32)
    try:
        print('Building isolated application image',flush=True)
        subprocess.run(['docker','build','-t',image,'.'],check=True)
        docker('network','create',network)
        for name in volumes:docker('volume','create',name)
        common=['--network',network,'--network-alias','harbor','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=16m','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','2g','--cpus','2','--pids-limit','192','-v',data+':/data']
        docker('run','-d','--name',backend,*common,'-p','127.0.0.1::8080','-e','APP_MODE=live','-e','BOOTSTRAP_TOKEN',image,env=env)
        port=int(docker('port',backend,'8080/tcp').stdout.strip().rsplit(':',1)[1]);local=Client(port);wait_health(local)
        print('Checking bootstrap and MFA enrollment',flush=True)
        local.call('/api/setup',{'password':password,'bootstrap_token':env['BOOTSTRAP_TOKEN']},expected=201)
        local.call('/api/login',{'user':'admin','token':password});local.csrf=local.call('/api/state')[0]['csrf']
        enrollment=local.call('/api/security/mfa/begin',{'user':'admin','password':password})[0]
        recovery=local.call('/api/security/mfa/confirm',{'user':'admin','password':password,'code':totp(enrollment['secret'])})[0]['recovery_codes']
        docker('rm','-f',backend)
        docker('run','-d','--name',backend,*common,'-e','APP_MODE=live','-e','EXPOSURE_MODE=public','-e','PUBLIC_ORIGIN=https://harbor.test','-e','COOKIE_SECURE=1',image)
        assert not docker('port',backend).stdout.strip(),'Public backend unexpectedly publishes a host port'
        with tempfile.TemporaryDirectory() as temp:
            config=Path(temp)/'Caddyfile';config.write_text(Path('deploy/Caddyfile').read_text().replace('{$HARBOR_DOMAIN} {','{$HARBOR_DOMAIN} {\n    tls internal'))
            docker('run','-d','--name',gateway,'--network',network,'-p','127.0.0.1::443','-e','HARBOR_DOMAIN=harbor.test','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=16m','--tmpfs','/config:rw,noexec,nosuid,size=16m','--cap-drop','ALL','--cap-add','NET_BIND_SERVICE','--security-opt','no-new-privileges','-v',str(config)+':/etc/caddy/Caddyfile:ro','-v',tls+':/data','caddy:2.11.7')
            ca=Path(temp)/'root.crt'
            for _ in range(60):
                if docker('cp',gateway+':/data/caddy/pki/authorities/local/root.crt',str(ca),check=False).returncode==0:break
                time.sleep(.5)
            assert ca.is_file(),'Local fixture CA was not generated'
            port=int(docker('port',gateway,'443/tcp').stdout.strip().rsplit(':',1)[1]);secure=Client(port,ssl.create_default_context(cafile=str(ca)));wait_health(secure)
            print('Checking verified TLS, setup denial, MFA and CSRF',flush=True)
            secure.call('/api/setup',{},expected=403)
            _,headers=secure.call('/api/login',{'user':'admin','token':password,'code':recovery[0]})
            assert 'Secure' in headers.get('Set-Cookie',''),'Secure cookie missing'
            state,headers=secure.call('/api/state');secure.csrf=state['csrf']
            assert 'max-age=' in headers.get('Strict-Transport-Security',''),'HSTS missing'
            assert "img-src 'self' data:" in headers.get('Content-Security-Policy',''),'Image CSP missing'
            secure.call('/api/tasks',{},expected=403,csrf=False)
            posture=secure.call('/api/security/posture')[0]
            assert posture['pdf_hard_memory_limit'] and posture['mfa']['admin'],'Linux/MFA posture mismatch'
            secure.call('/api/login',{'user':'admin','token':password,'code':recovery[0]},expected=403)
            print('Checking isolated Linux PDF extraction',flush=True)
            uploaded=secure.call('/api/files',{'name':'smoke.pdf','data':base64.b64encode(pdf()).decode()},expected=201)[0]
            evidence=secure.call('/api/files/'+uploaded['id'])[0]
            assert 'Local smoke evidence' in json.dumps(evidence),'PDF evidence text missing'
        print('PASS: Linux container, verified local TLS, MFA/recovery replay, CSRF, backend isolation, headers and PDF',flush=True)
    finally:
        for name in containers:docker('rm','-f',name,check=False)
        for name in volumes:docker('volume','rm',name,check=False)
        docker('network','rm',network,check=False);docker('image','rm',image,check=False)


if __name__=='__main__':main()
