"""CPU speech enhancement / vocal extraction; originals are never overwritten."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voiceclone import audio


def process(spec, emit):
    import imageio_ffmpeg
    import numpy as np
    import sherpa_onnx as sherpa
    import soundfile as sf
    root, directory = Path(spec['root']), Path(spec['directory'])
    os.chdir(root)
    emit(state='processing', message='读取所选音频片段')
    clipped = directory / 'selected.wav'
    audio.crop(spec['source'], clipped, spec['start'], spec['end'])
    original, rate = sf.read(clipped, dtype='float32', always_2d=True)
    voice = original
    if spec['mode'] in ['music', 'both']:
        emit(state='processing', message='正在分离人声与背景音乐（CPU）')
        config = sherpa.OfflineSourceSeparationConfig(model=sherpa.OfflineSourceSeparationModelConfig(
            uvr=sherpa.OfflineSourceSeparationUvrModelConfig(model='models/audio/UVR_MDXNET_9482.onnx'),
            num_threads=2, debug=False, provider='cpu'))
        if not config.validate():
            raise RuntimeError('音乐分离模型配置无效，请重新运行“安装音频处理.cmd”。')
        separator = sherpa.OfflineSourceSeparation(config)
        result = separator.process(sample_rate=rate, samples=np.ascontiguousarray(original.T))
        if len(result.stems) != 2:
            raise RuntimeError('分离模型未返回两条音轨。')
        voice = np.asarray(result.stems[0].data, dtype='float32').T
        voice_rate = result.sample_rate
        del separator, result
    else:
        voice_rate = rate
    if spec['mode'] in ['noise', 'both']:
        emit(state='processing', message='正在清理风声、底噪等环境音（CPU）')
        config = sherpa.OfflineSpeechDenoiserConfig(model=sherpa.OfflineSpeechDenoiserModelConfig(
            gtcrn=sherpa.OfflineSpeechDenoiserGtcrnModelConfig(model='models/audio/gtcrn_simple.onnx'),
            num_threads=2, debug=False, provider='cpu'))
        if not config.validate():
            raise RuntimeError('环境降噪模型配置无效，请重新运行“安装音频处理.cmd”。')
        denoiser = sherpa.OfflineSpeechDenoiser(config)
        result = denoiser(np.ascontiguousarray(voice.mean(axis=1)), voice_rate)
        voice = np.asarray(result.samples, dtype='float32').reshape(-1, 1)
        voice_rate = result.sample_rate
        del denoiser, result
    emit(state='processing', message='保存人声音轨和背景音轨')
    if voice_rate != rate:
        interim, resampled = directory / 'voice-interim.wav', directory / 'voice-resampled.wav'
        sf.write(interim, voice, voice_rate, subtype='FLOAT')
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y', '-i', str(interim),
                        '-ar', str(rate), '-c:a', 'pcm_f32le', str(resampled)], check=True, capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        voice, _ = sf.read(resampled, dtype='float32', always_2d=True)
    if len(voice) < len(original):
        voice = np.pad(voice, ((0, len(original) - len(voice)), (0, 0)))
    voice = voice[:len(original)]
    if voice.shape[1] != original.shape[1]:
        voice = np.repeat(voice.mean(axis=1, keepdims=True), original.shape[1], axis=1)
    if not np.isfinite(voice).all():
        raise RuntimeError('模型输出包含无效采样。')
    background = original - voice
    gain = max(1.0, float(np.max(np.abs(voice))), float(np.max(np.abs(background))))
    sf.write(spec['vocals'], voice / gain, rate, subtype='PCM_16')
    sf.write(spec['background'], background / gain, rate, subtype='PCM_16')
    vocals_info, background_info = audio.describe(spec['vocals']), audio.describe(spec['background'])
    vocals_info['download'] = vocals_info['url'] + '?download=vocals'
    background_info['download'] = background_info['url'] + '?download=background'
    return {'vocals': vocals_info, 'background': background_info,
            'message': '处理完成。背景为移除成分，可能残留人声；请分别试听。'}


def main():
    spec = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    path = Path(spec['directory']) / 'status.json'
    def emit(**values):
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(values, ensure_ascii=False), encoding='utf-8')
        temp.replace(path)
    try:
        result = process(spec, emit)
        emit(state='ready', finished_at=time.time(), **result)
    except Exception as exc:
        traceback.print_exc()
        emit(state='error', message=str(exc), finished_at=time.time())
        raise SystemExit(1)


if __name__ == '__main__':
    main()
