#!/usr/bin/env python3
"""Optional manual node reporter. No commands or jobs are fetched from Harbor."""
import ipaddress
import json
import os
from pathlib import Path
import time
import urllib.request
from urllib.parse import urlparse


def main():
    url=os.environ['HARBOR_NODE_URL'];parsed=urlparse(url)
    try:loopback=ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:loopback=parsed.hostname=='localhost'
    if parsed.scheme!='https' and not (parsed.scheme=='http' and loopback) or parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path.startswith('/api/node-heartbeats/'):
        raise SystemExit('Use HTTPS or loopback HTTP and the exact heartbeat path, without URL credentials.')
    file=Path(os.environ['HARBOR_NODE_TOKEN_FILE'])
    if file.stat().st_mode&0o077:raise SystemExit('Token file must be owner-only (chmod 600).')
    token=file.read_text().strip()
    if not 16<=len(token)<=512:raise SystemExit('Invalid token file.')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(NoRedirect)
    while True:
        metrics={'status':'online','running_jobs':int(os.getenv('HARBOR_RUNNING_JOBS','0'))}
        if hasattr(os,'getloadavg'):metrics['load_1m']=os.getloadavg()[0]
        request=urllib.request.Request(url,data=json.dumps(metrics).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token},method='POST')
        try:
            with opener.open(request,timeout=10) as response:
                if response.status!=200:raise ValueError('rejected')
        except Exception:print('Heartbeat failed; check connectivity, enrollment and token.',flush=True)
        time.sleep(30)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
