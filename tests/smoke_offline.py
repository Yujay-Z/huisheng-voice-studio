"""Exercise the installed payload with external Python networking blocked.

Only run against a disposable installation inside this workspace's .cache.
If the development studio is idle, temporarily stop and restore it so the
single GPU never has to hold two copies of IndexTTS at the same time.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
NETWORK_GUARD = '''import socket
_connect=socket.socket.connect
_connect_ex=socket.socket.connect_ex
def _allowed(address):
    return not isinstance(address,tuple) or address[0] in ('127.0.0.1','::1','localhost')
def _offline_connect(self,address):
    if not _allowed(address): raise OSError('External network disabled by offline smoke test')
    return _connect(self,address)
def _offline_connect_ex(self,address):
    if not _allowed(address): return 10051
    return _connect_ex(self,address)
socket.socket.connect=_offline_connect
socket.socket.connect_ex=_offline_connect_ex
'''


def request(base, path, body=None, token=None, raw=False):
    headers = {}
    if token:
        headers['X-Local-Token'] = token
    if body is not None:
        if isinstance(body, bytes):
            headers['X-Filename'] = 'offline-check.wav'
        else:
            body = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
    try:
        with urlopen(Request(base + path, data=body, headers=headers), timeout=10) as response:
            content = response.read()
    except HTTPError as error:
        raise RuntimeError(path + ': ' + error.read().decode('utf-8',errors='replace')) from error
    return content if raw else json.loads(content)


def wait_until(fn, seconds, label):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.25)
    raise RuntimeError('Timed out: ' + label)


def alive_state(base):
    try:
        return request(base, '/api/state')
    except (URLError, OSError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    target = args.root.resolve()
    if not target.is_relative_to(ROOT / '.cache') or not (target / 'offline-package.json').is_file():
        raise RuntimeError('Only a disposable offline installation inside .cache is allowed.')
    original = 'http://127.0.0.1:7865'
    previous = alive_state(original)
    active = {'submitting', 'queued', 'loading', 'generating', 'combining'}
    if previous and any(j['state'] in active or any(v.get('transcription_state') in ['queued', 'processing'] for v in j.get('variants', [])) for j in previous['jobs']):
        raise RuntimeError('Development studio has active work; it was not stopped.')
    import soundfile as sf
    sources = sorted((ROOT / 'data/jobs').glob('*/candidate.wav'), key=lambda p:p.stat().st_mtime, reverse=True)
    source = next((p for p in sources if 1 <= sf.info(p).duration <= 6), None)
    if source is None:
        raise RuntimeError('An existing short reference is required for the local smoke test.')
    config_path = target / 'config.json'
    old_config = config_path.read_bytes()
    config = json.loads(old_config)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    config['port'] = port
    temporary = config_path.with_suffix('.smoke.tmp')
    temporary.write_text(json.dumps(config), encoding='utf-8')
    temporary.replace(config_path)
    guard = target / 'sitecustomize.py'
    if guard.exists():
        raise RuntimeError('Unexpected sitecustomize.py; test did not overwrite it.')
    guard.write_text(NETWORK_GUARD, encoding='utf-8')
    base = f'http://127.0.0.1:{port}'
    process = None
    original_stopped = False
    log = None
    result = {'passed': False, 'external_python_network_blocked': True}
    try:
        if previous:
            request(original, '/api/shutdown', {}, previous['token'])
            original_stopped = True
            wait_until(lambda: not alive_state(original), 15, 'development studio shutdown')
            time.sleep(2)
        logs = target / 'logs'
        logs.mkdir(exist_ok=True)
        log = (logs / 'offline-smoke-server.log').open('wb')
        process = subprocess.Popen([str(target / 'runtime/app/python.exe'), '-u', str(target / 'app.py'), '--no-browser'],
                                   cwd=target, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, 'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'},
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        state = wait_until(lambda: alive_state(base), 30, 'portable backend')
        token = state['token']
        def ready():
            current = request(base, '/api/model')
            if current['state'] == 'error':
                raise RuntimeError('Portable model preload failed: ' + current.get('message',''))
            return current['state'] == 'ready'
        wait_until(ready, 180, 'portable offline model preload')
        print('Portable model loaded with external networking blocked.', flush=True)
        uploaded = request(base, '/api/upload', source.read_bytes(), token)
        assert len(uploaded['peaks']) > 0
        cropped = request(base, '/api/audio/crop', {'reference':uploaded['id'],'start':0,'end':min(1.2,uploaded['seconds'])}, token)
        assert 1 <= cropped['seconds'] <= 1.21 and cropped['id'] != uploaded['id']
        result.update(waveform=True, crop=True)
        separation = []
        for mode in ['noise','music','both']:
            task = request(base, '/api/audio/separate', {'reference':cropped['id'],'start':0,'end':cropped['seconds'],'mode':mode}, token)
            def separated():
                item = request(base, '/api/audio/task/' + task['id'])
                if item['state'] in ['error','cancelled']:
                    raise RuntimeError('Separation failed: ' + item['message'])
                return item if item['state'] == 'ready' else None
            item = wait_until(separated, 60, 'audio separation ' + mode)
            for stem in ['vocals','background']:
                raw = request(base, item[stem]['download'], raw=True)
                assert sf.info(io.BytesIO(raw)).duration >= 1
            separation.append(mode)
        result['separation_modes'] = separation
        print('Waveform, crop, noise/music/combined separation and background exports passed.', flush=True)
        take_results = []
        for language,text in [('ZH','你好，这是离线安装测试。'),('EN','Hello, this is an offline test.')]:
            job = request(base, '/api/jobs', {'reference':cropped['id'],'text':text,'language':language}, token)
            early = False
            def finished():
                nonlocal early
                current = request(base, '/api/job/' + job['id'])
                if current['state'] in ['error','cancelled']:
                    raise RuntimeError(current['message'])
                if current['variants'][0].get('generated') and current['state'] in active:
                    raw = request(base,current['variants'][0]['download'],raw=True)
                    assert len(raw)>44
                    early = True
                done = current['state']=='ready' and all(v.get('transcription_state') not in ['waiting','queued','processing'] for v in current['variants'])
                return current if done else None
            final = wait_until(finished, 180, 'three takes and transcription ' + language)
            hashes = []
            assert len(final['variants']) == 3
            assert len({v['seed'] for v in final['variants']}) == 3
            assert all(v['generated'] and v['transcription_state']=='ready' for v in final['variants'])
            for number, variant in enumerate(final['variants']):
                assert variant['number'] == number + 1
                raw = request(base,variant['download'],raw=True)
                assert sf.info(io.BytesIO(raw)).duration > .15
                hashes.append(hashlib.sha256(raw).hexdigest())
            take_results.append({'language':language,'takes':3,'distinct_files':len(set(hashes)),
                                 'transcriptions':3,'early_download':early,
                                 'model_reused':all(v.get('model_reused') for v in final['variants'])})
            print(language + ': three independent audio downloads and three local transcripts passed.', flush=True)
        result['generation'] = take_results
        # Verify desktop shortcut output in a test subfolder, never on the real desktop.
        shortcut_folder = target / '.shortcut-test'
        check = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(target/'scripts/create-shortcut.ps1'),
                                '-Destination',str(shortcut_folder),'-ShortcutName','Offline test'],
                               cwd=target,capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if check.returncode:
            raise RuntimeError('Shortcut creation failed: ' + check.stderr.decode(errors='replace'))
        assert (shortcut_folder/'Offline test.lnk').is_file()
        result.update(shortcut=True,passed=True)
    finally:
        if process:
            try:
                state = alive_state(base)
                if state:
                    request(base, '/api/shutdown', {}, state['token'])
                process.wait(timeout=20)
            except Exception:
                process.terminate()
                process.wait(timeout=10)
        if log:
            log.close()
        temporary.write_bytes(old_config)
        temporary.replace(config_path)
        guard.unlink(missing_ok=True)
        if original_stopped:
            cache = ROOT / '.cache/offline-build'
            snapshot_path = cache / 'development-state.json'
            snapshot_path.write_text(json.dumps(previous['jobs']),encoding='utf-8')
            restore = cache / 'restore-development.py'
            restore.write_text('''import json,sys,threading
from pathlib import Path
root=Path.cwd()
sys.path.insert(0,str(root))
from voiceclone import service
for job in reversed(json.loads((root/'.cache/offline-build/development-state.json').read_text(encoding='utf-8'))):
    job['_cancel']=threading.Event()
    job['_process']=None
    service.JOBS[job['id']]=job
sys.argv=[str(root/'app.py'),'--no-browser']
from app import main
main()
''',encoding='utf-8')
            with (ROOT / 'logs/studio-restored.log').open('ab') as output:
                subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),'-u',str(restore),str(ROOT/'app.py')],cwd=ROOT,
                                 stdout=output,stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            wait_until(lambda: alive_state(original),30,'restore development backend')
            wait_until(lambda: request(original,'/api/model')['state']=='ready',180,'restore resident development model')
            result['development_studio_restored'] = True
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
