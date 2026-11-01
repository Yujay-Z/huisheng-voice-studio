"""Manual CPU-only smoke test with installed models and an existing local voice clip."""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from voiceclone.audio_worker import process


def main():
    import imageio_ffmpeg
    import numpy as np
    import soundfile as sf
    sources = sorted((ROOT / 'data/jobs').glob('*/candidate.wav'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not sources:
        raise RuntimeError('No existing local voice clip is available for this smoke test.')
    directory = ROOT / '.cache/audio-smoke' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    source = directory / 'source.wav'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y', '-i', str(sources[0]),
                    '-t', '2', '-ar', '44100', '-ac', '2', str(source)], check=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    samples, rate = sf.read(source, dtype='float32', always_2d=True)
    noise = np.random.default_rng(2026).normal(0, .008, samples.shape)
    music = .02 * np.sin(np.arange(len(samples)) * (2 * np.pi * 220 / rate))
    sf.write(source, samples + noise + music[:, None], rate, subtype='PCM_16')
    original = sf.read(source, dtype='float32', always_2d=True)[0]
    result = {}
    for mode in ['noise', 'music', 'both']:
        task_dir = directory / mode
        task_dir.mkdir()
        started = time.perf_counter()
        output = process({'root': str(ROOT), 'directory': str(task_dir), 'source': str(source), 'start': 0,
                          'end': len(samples) / rate, 'mode': mode,
                          'vocals': str(task_dir / 'vocals.wav'), 'background': str(task_dir / 'background.wav')},
                         lambda **values: print(mode + ': ' + values.get('message', ''), flush=True))
        vocals, vr = sf.read(task_dir / 'vocals.wav', dtype='float32', always_2d=True)
        background, br = sf.read(task_dir / 'background.wav', dtype='float32', always_2d=True)
        assert vocals.shape == background.shape == original.shape
        assert vr == br == rate
        assert np.isfinite(vocals).all() and np.isfinite(background).all()
        assert np.max(np.abs(vocals + background - original)) < .0001
        result[mode] = {'seconds': round(time.perf_counter() - started, 2), 'duration': output['vocals']['seconds'],
                        'vocal_peak': round(float(np.max(np.abs(vocals))), 5)}
    (directory / 'summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    print('Saved under ' + str(directory), flush=True)


if __name__ == '__main__':
    main()
