"""Exercise the installed audio tools through the running local HTTP service."""
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:7865'


def main():
    with urllib.request.urlopen(BASE + '/api/state', timeout=5) as response:
        state = json.load(response)
    assert state['build'] == 'editor-variants-v1'
    assert state['audio_processing']['ready']
    def post(path, body, raw=False):
        request = urllib.request.Request(BASE + path, data=body if raw else json.dumps(body).encode(), headers={
            'X-Local-Token': state['token'], 'X-Filename': 'test.wav', 'Content-Type': 'audio/wav' if raw else 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    source = max((ROOT / '.cache/audio-smoke').glob('*/source.wav'), key=lambda path: path.stat().st_mtime)
    uploaded = post('/api/upload', source.read_bytes(), raw=True)
    assert uploaded['channels'] == 2 and uploaded['peaks']
    clipped = post('/api/audio/crop', {'reference': uploaded['id'], 'start': .2, 'end': 1.7})
    assert abs(clipped['seconds'] - 1.5) < .0001
    task = post('/api/audio/separate', {'reference': clipped['id'], 'start': 0, 'end': clipped['seconds'], 'mode': 'both'})
    deadline = time.monotonic() + 60
    while task['state'] not in ['ready', 'error', 'cancelled']:
        if time.monotonic() > deadline:
            raise RuntimeError('Audio processing API timed out')
        time.sleep(.2)
        with urllib.request.urlopen(BASE + '/api/audio/task/' + task['id'], timeout=5) as response:
            task = json.load(response)
    assert task['state'] == 'ready', task
    for key in ['vocals', 'background']:
        assert abs(task[key]['seconds'] - 1.5) < .0001
        with urllib.request.urlopen(BASE + task[key]['download'], timeout=5) as response:
            assert response.headers['Content-Type'] == 'audio/wav'
            assert response.headers.get('Content-Disposition'), 'download should be an attachment'
            assert response.read(4) == b'RIFF'
    print(json.dumps({'upload_waveform': len(uploaded['peaks']), 'crop_seconds': clipped['seconds'],
                      'separation_state': task['state'], 'both_downloads': 'OK'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
