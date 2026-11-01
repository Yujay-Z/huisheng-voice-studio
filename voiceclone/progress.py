"""Worker events and explicitly labelled runtime estimates."""
import json
import math
from pathlib import Path
import threading
import time

TERMINAL = {'ready', 'error', 'cancelled'}
DEFAULT_RATES = {'loading': 60.0, 'synthesis': 1.0}
PHASES = {'loading': (0, 15), 'generating': (15, 98), 'combining': (98, 99)}


class WorkerProgress:
    def __init__(self, path):
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.stop = threading.Event()
    def __enter__(self):
        self.thread = threading.Thread(target=self.heartbeat, daemon=True)
        self.thread.start()
        return self
    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=1)
    def emit(self, kind='progress', **values):
        if self.path:
            with self.lock, self.path.open('a', encoding='utf-8') as output:
                output.write(json.dumps({'kind': kind, 'at': time.time(), **values}, ensure_ascii=False) + '\n')
    def heartbeat(self):
        while not self.stop.wait(3):
            self.emit('heartbeat')


class EventReader:
    def __init__(self, path):
        self.path, self.offset, self.pending = Path(path), 0, b''
    def read(self):
        if not self.path.exists():
            return []
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            self.pending += stream.read()
            self.offset = stream.tell()
        lines = self.pending.split(b'\n')
        self.pending = lines.pop()
        result = []
        for line in lines:
            if line.strip():
                try:
                    result.append(json.loads(line))
                except ValueError:
                    pass
        return result


def read_profile(directory, engine):
    path = Path(directory) / 'performance.json'
    try:
        return json.loads(path.read_text(encoding='utf-8')).get(engine, {})
    except (OSError, ValueError):
        return {}


def record_metric(directory, engine, metric, seconds, units=1):
    if metric not in DEFAULT_RATES or not math.isfinite(seconds) or seconds <= 0 or units <= 0:
        return
    path = Path(directory) / 'performance.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    profile = data.setdefault(engine, {})
    old = profile.get(metric, {})
    value = seconds / units
    profile[metric] = {'value': value if not old else old['value'] * 0.7 + value * 0.3,
                       'samples': old.get('samples', 0) + 1}
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temp.replace(path)


def phase_info(job, phase, fraction=0, now=None):
    now = time.time() if now is None else now
    profile = job.get('_profile', {})
    rates = {key: profile.get(key, {}).get('value', default) for key, default in DEFAULT_RATES.items()}
    chars = max(1, sum(len(s['text']) for s in job['segments']))
    synthesis = chars * rates['synthesis']
    budgets = {'loading': (rates['loading'], synthesis + 1),
               'generating': (synthesis, 1), 'combining': (1, 0)}
    previous = job.get('progress', {})
    started = previous.get('stage_started_at', now) if previous.get('phase') == phase else now
    base, end = PHASES[phase]
    clamped = max(0, min(1, fraction))
    expected, after = budgets[phase]
    return {'phase': phase, 'fraction': clamped, 'percent': round(base + (end - base) * clamped),
            'stage_started_at': started, 'expected_stage_seconds': expected,
            'remaining_after_stage': after, 'indeterminate': phase == 'loading',
            'estimate_source': '含本机实测' if profile.get('synthesis') else '首次粗估'}


def snapshot(job, now=None):
    now = time.time() if now is None else now
    info = dict(job.get('progress', {}))
    end = job.get('finished_at', now)
    info['elapsed_seconds'] = max(0, int(end - job.get('started_at', job.get('created', now))))
    if job['state'] in TERMINAL:
        info.update(eta_seconds=None, indeterminate=False)
        if job.get('generated'):
            info['percent'] = 100
    elif all(key in info for key in ('stage_started_at', 'expected_stage_seconds', 'remaining_after_stage')):
        stage_elapsed = max(0, now - info['stage_started_at'])
        expected = info['expected_stage_seconds']
        # Do not leave a bogus "0 seconds remaining" while a stage is still busy.
        info['eta_seconds'] = math.ceil(expected - stage_elapsed + info['remaining_after_stage']) if stage_elapsed <= expected else None
    else:
        info.update(percent=0, eta_seconds=None, estimate_source='正在估算', indeterminate=True)
    heartbeat = job.get('_last_heartbeat')
    info['last_response_seconds'] = max(0, int(now - heartbeat)) if heartbeat else None
    return info
