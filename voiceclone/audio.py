"""Local waveform inspection and non-destructive, sample-accurate cropping."""
import math
from pathlib import Path
import uuid


def describe(path, ident=None):
    import numpy as np
    import soundfile as sf
    peaks = []
    with sf.SoundFile(path) as stream:
        seconds = stream.frames / stream.samplerate
        block = max(1, math.ceil(stream.frames / 640))
        for samples in stream.blocks(blocksize=block, dtype='float32', always_2d=True):
            if not np.isfinite(samples).all():
                raise ValueError('音频包含无效采样。')
            peaks.append(float(np.max(np.abs(samples))))
        result = {'seconds': seconds, 'sample_rate': stream.samplerate,
                  'channels': stream.channels, 'short_reference': seconds < 3}
    scale = max(max(peaks, default=0), .000001)
    result['peaks'] = [round(value / scale, 4) for value in peaks]
    result['peak_amplitude'] = scale
    ident = ident or Path(path).stem
    result.update(id=ident, url='/audio/reference/' + ident,
                  download='/audio/reference/' + ident + '?download=1')
    return result


def selection(path, start, end, maximum=30):
    import soundfile as sf
    info = sf.info(path)
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        raise ValueError('请输入有效的开始、结束时间。') from None
    duration = info.frames / info.samplerate
    if not all(math.isfinite(v) for v in [start, end]) or start < 0 or end > duration + .001:
        raise ValueError('选区超出音频范围。')
    first, last = round(start * info.samplerate), min(info.frames, round(end * info.samplerate))
    if last - first < info.samplerate or last - first > maximum * info.samplerate:
        raise ValueError(f'请选择 1–{maximum} 秒的音频片段。')
    return first, last, info.samplerate


def crop(source, dest, start, end):
    import soundfile as sf
    first, last, rate = selection(source, start, end)
    with sf.SoundFile(source) as stream:
        stream.seek(first)
        samples = stream.read(last - first, dtype='float32', always_2d=True)
    sf.write(dest, samples, rate, subtype='PCM_16')
    return describe(dest)


def crop_upload(source, directory, start, end):
    ident = uuid.uuid4().hex
    dest = Path(directory) / (ident + '.wav')
    try:
        return crop(source, dest, start, end)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
