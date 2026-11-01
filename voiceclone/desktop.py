"""Open the existing local studio or start it without a console window."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser
from voiceclone.runtime import app_python, configure_runtime


def backend_running(url):
    try:
        with urllib.request.urlopen(url + '/api/state', timeout=1) as response:
            state = json.loads(response.read(4 * 1024 * 1024))
        return isinstance(state, dict) and state.get('api_version') == 3 and isinstance(state.get('engines'), dict) and 'index' in state['engines']
    except (OSError, ValueError):
        return False


def open_studio(root):
    root = Path(root)
    configure_runtime(root)
    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
    url = f'http://127.0.0.1:{config["port"]}'
    if backend_running(url):
        webbrowser.open(url)
        return
    bundled = app_python(root)
    python = bundled if bundled.is_file() else Path(sys.executable).with_name('python.exe')
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    with (logs / 'studio.log').open('ab', buffering=0) as output:
        process = subprocess.Popen([str(python), '-u', str(root / 'app.py'), '--no-browser'],
                                   cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if backend_running(url):
            webbrowser.open(url)
            return
        if process.poll() is not None:
            raise RuntimeError('启动失败，请查看项目 logs/studio.log。若有旧启动窗口，请先关闭它再双击桌面图标。')
        time.sleep(.25)
    raise RuntimeError('启动超时，请查看项目 logs/studio.log，稍后再双击桌面图标。')


def main(root):
    root = Path(root)
    # A second click while startup is in progress should not spawn another server.
    from voiceclone.downloads import download_lock
    try:
        with download_lock(root / '.cache/studio-launch.lock', busy_message='音色工作室正在启动'):
            open_studio(root)
    except RuntimeError as exc:
        if str(exc) == '音色工作室正在启动':
            return
        ctypes.windll.user32.MessageBoxW(None, str(exc), '音色工作室', 0x10)
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, '无法启动：' + str(exc), '音色工作室', 0x10)
