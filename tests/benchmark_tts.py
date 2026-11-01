"""Measure two local, warm-model requests through the actual studio API."""
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:7865'


def request(path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Local-Token'] = token
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=headers), timeout=15) as response:
        return json.load(response)


def main():
    import soundfile as sf
    state = request('/api/state')
    if state.get('build') != 'editor-variants-v1':
        raise RuntimeError('Restart the studio to use the updated backend first.')
    if any(job['state'] in ['queued', 'loading', 'generating', 'combining'] for job in state['jobs']):
        raise RuntimeError('A user generation task is running; the benchmark was not started.')
    sources = sorted((ROOT / 'data/jobs').glob('*/request.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    reference = next((Path(json.loads(path.read_text(encoding='utf-8'))['reference']) for path in sources
                      if json.loads(path.read_text(encoding='utf-8')).get('reference')), None)
    if reference is None or not reference.exists():
        raise RuntimeError('No existing local reference is available.')
    started = time.perf_counter()
    deadline = time.monotonic() + 150
    while True:
        model = request('/api/model')
        if model['state'] == 'ready':
            break
        if model['state'] in ['error', 'idle'] or time.monotonic() > deadline:
            raise RuntimeError('Model preparation did not finish: ' + str(model))
        time.sleep(.5)
    print(f'Model ready (waited {time.perf_counter() - started:.2f}s in this check)', flush=True)
    summaries = []
    for run in range(2):
        started = time.perf_counter()
        job = request('/api/jobs', {'reference': reference.stem, 'text': '长了一副好皮囊，', 'language': 'ZH'}, state['token'])
        deadline = time.monotonic() + 120
        while job['state'] not in ['ready', 'error', 'cancelled']:
            if time.monotonic() > deadline:
                raise RuntimeError('Benchmark task timed out: ' + job['id'])
            time.sleep(.2)
            job = request('/api/job/' + job['id'])
        if job['state'] != 'ready':
            raise RuntimeError(job['message'])
        audio = sf.info(ROOT / 'data/jobs' / job['id'] / 'candidate.wav')
        summary = {'run': run + 1, 'id': job['id'], 'elapsed_seconds': round(time.perf_counter() - started, 2),
                   'audio_seconds': round(audio.duration, 2), 'model_reused': job.get('model_reused'), 'timings': job.get('timings')}
        assert summary['model_reused'] is True
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    path = ROOT / '.cache/tts-speed-check.json'
    path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved: ' + str(path), flush=True)


if __name__ == '__main__':
    main()
