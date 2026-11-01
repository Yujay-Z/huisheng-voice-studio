"""Offline runtime/model check. Does not synthesize speech or fetch resources."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from voiceclone.runtime import configure_runtime, engine_python
configure_runtime(ROOT)


def engine_check():
    import torch
    import torchaudio
    from voiceclone.models import require_index_files
    from voiceclone.native_paths import preload_wetext
    require_index_files(ROOT / 'engines/index/checkpoints')
    if not torch.cuda.is_available():
        raise RuntimeError('无法初始化 NVIDIA CUDA。请确认目标电脑装有支持所带 CUDA 12.8 的 NVIDIA 显卡驱动。驱动不属于软件模型下载。')
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError('当前显卡不支持本版本所需的 BF16。离线版面向支持 BF16 的 NVIDIA 显卡，建议 RTX 30/40/50 系列、8 GB 或以上显存。')
    os.chdir(ROOT / 'engines/index')
    sys.path.insert(0, str(ROOT / 'engines/index'))
    preload_wetext()
    from indextts.infer_v2_5 import IndexTTS2
    return {'torch': torch.__version__, 'cuda': torch.version.cuda,
            'gpu': torch.cuda.get_device_name(0),
            'vram_gib': round(torch.cuda.get_device_properties(0).total_memory / 2**30, 2)}


def app_check():
    import numpy
    import soundfile
    import imageio_ffmpeg
    import sherpa_onnx
    from faster_whisper import WhisperModel
    from voiceclone.models import find_local_asr
    from voiceclone.separation import readiness
    asr = find_local_asr(ROOT, 'small')
    if not asr:
        raise RuntimeError('离线识别权重不完整。')
    WhisperModel(str(asr), device='cpu', compute_type='int8', local_files_only=True,
                 cpu_threads=2, num_workers=1)
    processing = readiness(ROOT)
    if not processing['ready']:
        raise RuntimeError(processing['message'])
    if not Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file():
        raise RuntimeError('离线 FFmpeg 不完整。')
    return {'asr': 'local small/int8', 'audio_processing': processing, 'ffmpeg': 'bundled'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        if args.engine:
            result = engine_check()
        else:
            result = app_check()
            child = subprocess.run([str(engine_python(ROOT)), str(Path(__file__).resolve()), '--engine', '--json'],
                                   cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if child.returncode:
                raise RuntimeError(child.stdout.strip() + '\n' + child.stderr.strip())
            result['engine_output'] = child.stdout.strip()
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
