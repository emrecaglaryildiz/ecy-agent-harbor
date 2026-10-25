"""CI-only synthetic PDF runtime probe; never runs on user uploads or provider data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.exports import pdf_bytes


def main():
    raw=pdf_bytes('# PDF smoke fixture\nLocal synthetic evidence.')
    # Exercise the actual worker path and import-path isolation used in production.
    # Copying the script elsewhere hides collisions such as app/platform.py.
    script=Path('app/pdf_worker.py').resolve()
    env={k:os.environ[k] for k in ('PATH','LANG','SYSTEMROOT') if k in os.environ}
    with tempfile.TemporaryDirectory(prefix='harbor-pdf-probe-') as folder:
        result=subprocess.run([sys.executable,'-E',str(script)],input=raw,env=env,cwd=folder,capture_output=True,timeout=30)
    if result.returncode or 'text' not in json.loads(result.stdout or b'{}'):
        print('Synthetic isolated PDF worker failed:',result.returncode,flush=True)
        print(result.stderr.decode(errors='replace')[:8000],flush=True)
        raise SystemExit(1)
    print('Synthetic isolated PDF worker passed',flush=True)


if __name__=='__main__':main()
