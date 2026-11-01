"""Optional CPU audio processing, isolated from the resident TTS model."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from voiceclone import audio

TASKS = {}
ACTIVE = threading.Lock()
LOCK = threading.RLock()
FILES = {'noise': ('gtcrn_simple.onnx', 535638), 'music': ('UVR_MDXNET_9482.onnx', 29704738)}


def readiness(root):
    files = Path(root) / 'models/audio'
    ready = importlib.util.find_spec('sherpa_onnx') is not None and all(
        (files / name).is_file() and (files / name).stat().st_size == size for name, size in FILES.values())
    return {'ready': ready, 'message': '音频处理已就绪（CPU）' if ready else '首次使用请运行“安装音频处理.cmd”'}


def status(task):
    with LOCK:
        result = {key: value for key, value in task.items() if not key.startswith('_')}
        path = task['_directory'] / 'status.json'
        if path.exists() and result['state'] not in ['error', 'cancelled']:
            result.update(json.loads(path.read_text(encoding='utf-8')))
            if result['state'] in ['ready', 'error'] and not task.get('_settled', True):
                result.update(state='processing', message='正在完成音频处理…')
                result.pop('finished_at', None)
        result['elapsed_seconds'] = int(max(0, result.get('finished_at', time.time()) - result['created']))
        return result


def start(root, data, source, payload):
    mode = payload.get('mode')
    if mode not in ['noise', 'music', 'both']:
        raise ValueError('请选择环境降噪、音乐分离或组合处理。')
    if not readiness(root)['ready']:
        raise ValueError(readiness(root)['message'])
    audio.selection(source, payload.get('start'), payload.get('end'))
    if not ACTIVE.acquire(blocking=False):
        raise ValueError('已有音频处理任务，请等待完成或取消。')
    try:
        ident = uuid.uuid4().hex
        directory = Path(data) / 'processing' / ident
        directory.mkdir(parents=True)
        task = {'id': ident, 'state': 'queued', 'message': '准备音频处理', 'created': time.time(),
                'mode': mode, '_directory': directory, '_cancel': threading.Event(), '_settled': False}
        vocals, background = uuid.uuid4().hex, uuid.uuid4().hex
        spec = {'root': str(root), 'source': str(source), 'start': payload['start'], 'end': payload['end'],
                'mode': mode, 'directory': str(directory), 'vocals': str(Path(data) / 'uploads' / (vocals + '.wav')),
                'background': str(Path(data) / 'uploads' / (background + '.wav'))}
        request = directory / 'request.json'
        request.write_text(json.dumps(spec), encoding='utf-8')
        with LOCK:
            TASKS[ident] = task
        initial = status(task)
        threading.Thread(target=run, args=(root, task, request), daemon=True).start()
        return initial
    except Exception:
        ACTIVE.release()
        raise


def run(root, task, request):
    process = None
    try:
        with (task['_directory'] / 'worker.log').open('a', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-u', str(Path(root) / 'voiceclone/audio_worker.py'), str(request)],
                                       cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                       env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'},
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            task['_process'] = process
            while process.poll() is None:
                if task['_cancel'].wait(.2) or time.time() - task['created'] > 600:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    if task['_cancel'].is_set():
                        task.update(state='cancelled', message='音频处理已取消。', finished_at=time.time())
                        return
                    raise RuntimeError('音频处理超过十分钟，已停止。')
            status_path = task['_directory'] / 'status.json'
            final = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
            if process.returncode or final.get('state') not in ['ready', 'error']:
                raise RuntimeError(final.get('message') if final.get('state') == 'error' else '音频处理进程退出，请查看 data/processing 中的 worker.log。')
            task.update(final, finished_at=time.time())
    except Exception as exc:
        task.update(state='error', message=str(exc), finished_at=time.time())
    finally:
        with LOCK:
            task['_process'] = None
            ACTIVE.release()
            task['_settled'] = True


def shutdown():
    for task in list(TASKS.values()):
        task['_cancel'].set()
        process = task.get('_process')
        if process and process.poll() is None:
            process.terminate()
