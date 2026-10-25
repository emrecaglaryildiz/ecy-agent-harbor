"""PDF worker supervision: wall time, concurrent jobs and bounded result file."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading

SLOTS=threading.BoundedSemaphore(2)
ERROR='PDF okunamadı veya güvenli işleme sınırını aştı; dosyayı bölerek tekrar dene'


def extract(raw):
    if os.getenv('PDF_UPLOADS')=='disabled' or os.getenv('EXPOSURE_MODE')=='public' and not sys.platform.startswith('linux'):
        raise ValueError('Dış erişimde PDF için Linux kaynak izolasyonu gerekli; metin veya görsel yükle')
    if not SLOTS.acquire(blocking=False):raise ValueError('PDF işleyici meşgul; tekrar dene')
    process=None;finished=threading.Event();exceeded=threading.Event()
    def terminate():
        if process and process.poll() is None:
            try:
                if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
                else:process.kill()
            except OSError:pass
    try:
        env={k:os.environ[k] for k in ('PATH','LANG','SYSTEMROOT') if k in os.environ}
        with tempfile.TemporaryDirectory(prefix='harbor-pdf-') as directory, tempfile.TemporaryFile(dir=directory) as output:
            process=subprocess.Popen([sys.executable,'-E',str(Path(__file__).with_name('pdf_worker.py'))],stdin=subprocess.PIPE,stdout=output,stderr=subprocess.DEVNULL,cwd=directory,env=env,start_new_session=True)
            def watch_memory():
                # Darwin has no effective RLIMIT_AS/DATA here. This is sampled supervision,
                # not a hard memory sandbox; public PDF service therefore requires Linux.
                while not finished.wait(.2):
                    try:
                        check=subprocess.run(['/bin/ps','-o','rss=','-p',str(process.pid)],capture_output=True,timeout=1)
                        if int(check.stdout.strip() or b'0')>512*1024:exceeded.set();terminate();return
                    except (ValueError,OSError,subprocess.TimeoutExpired):exceeded.set();terminate();return
            if sys.platform=='darwin':threading.Thread(target=watch_memory,daemon=True).start()
            try:process.communicate(raw,timeout=25)
            except subprocess.TimeoutExpired:terminate();process.communicate();raise ValueError(ERROR) from None
            if process.returncode or exceeded.is_set():raise ValueError(ERROR)
            output.seek(0);content=output.read(64*1024*1024+1)
            if len(content)>64*1024*1024:raise ValueError(ERROR)
            data=json.loads(content)
            if data.get('error') or not isinstance(data.get('text'),str):raise ValueError(ERROR)
            return data['text']
    except (OSError,ValueError,KeyError):raise ValueError(ERROR) from None
    finally:
        finished.set();terminate()
        if process:
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:pass
            if process.stdin:process.stdin.close()
        SLOTS.release()
