"""Keep the IndexTTS process alive; only one synthesis job runs at a time."""
import atexit
import os
from pathlib import Path
import subprocess
import threading


class ResidentWorker:
    def __init__(self):
        self.process = None
        self.command = None
        self.loaded = False
        self.lock = threading.RLock()

    def submit(self, python, script, request, root):
        with self.lock:
            command = (str(python), str(script), str(root))
            reused = (self.command == command and self.process is not None
                      and self.process.poll() is None)
            if not reused:
                self.close()
                log_path = Path(root) / 'logs/engine-worker.log'
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open('a', encoding='utf-8') as log:
                    self.process = subprocess.Popen(
                        [str(python), '-u', str(script), '--server'], stdin=subprocess.PIPE,
                        stdout=log, stderr=subprocess.STDOUT, cwd=root,
                        text=True, encoding='utf-8',
                        env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'},
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.command = command
            try:
                self.process.stdin.write(str(request) + '\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                self.close()
                raise RuntimeError('模型进程已退出，请重新提交任务。')
            return self.process, reused

    def mark_loaded(self, process):
        with self.lock:
            if process is self.process and process.poll() is None:
                self.loaded = True

    def ready(self):
        with self.lock:
            return bool(self.loaded and self.process is not None and self.process.poll() is None)

    def close(self):
        with self.lock:
            proc, self.process = self.process, None
            self.command = None
            self.loaded = False
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=5)
                finally:
                    if proc.stdin:
                        proc.stdin.close()


WORKER = ResidentWorker()
atexit.register(WORKER.close)
