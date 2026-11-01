"""Resident synthesis worker with per-task logs, events and completion markers."""
from contextlib import redirect_stdout, redirect_stderr
import json
import os
from pathlib import Path
import random
import sys
import time
import traceback

MODEL = None
MODEL_KEY = None


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from voiceclone.runtime import configure_runtime
    configure_runtime(Path(__file__).resolve().parent.parent)
    if sys.argv[1] == '--server':
        for line in sys.stdin:
            if line.strip() and not run_request(Path(line.strip())):
                # Discard a potentially damaged CUDA context after any failed task.
                return
    else:
        if not run_request(Path(sys.argv[1])):
            raise SystemExit(1)


def run_request(request):
    from voiceclone.progress import WorkerProgress
    spec = json.loads(request.read_text(encoding='utf-8'))
    success, error = False, None
    with (request.parent / 'engine.log').open('a', encoding='utf-8', buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            try:
                with WorkerProgress(spec.get('progress_path')) as progress:
                    if spec.get('warmup'):
                        get_model(spec, progress)
                    else:
                        generate(spec, progress)
                success = True
            except Exception as exc:
                error = str(exc)
                traceback.print_exc()
    if spec.get('result_path'):
        result = Path(spec['result_path'])
        temp = result.with_suffix('.tmp')
        temp.write_text(json.dumps({'ok': success, 'error': error}, ensure_ascii=False), encoding='utf-8')
        temp.replace(result)
    return success


def load_model(spec):
    repo = Path(spec['repo'])
    from voiceclone.models import require_index_files
    require_index_files(repo / 'checkpoints')
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    sys.path.append(str(repo / 'third_party/Matcha-TTS'))
    from voiceclone.native_paths import preload_wetext
    preload_wetext()
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('没有可用 CUDA 显卡，请检查驱动和 PyTorch。')
    from indextts.infer_v2_5 import IndexTTS2
    return IndexTTS2(cfg_path=str(repo / 'checkpoints/config.yaml'),
                     model_dir=str(repo / 'checkpoints'), use_bf16=True,
                     use_cuda_kernel=False, use_deepspeed=False,
                     use_accel=False, use_torch_compile=False, use_qwen_emo=False)


def get_model(spec, progress):
    global MODEL, MODEL_KEY
    key = spec['repo']
    if MODEL is None or MODEL_KEY != key:
        started = time.perf_counter()
        progress.emit(phase='loading', fraction=0, message='首次加载模型到显卡')
        MODEL = load_model(spec)
        MODEL_KEY = key
        progress.emit('metric', metric='loading', seconds=time.perf_counter() - started, units=1)
    else:
        progress.emit('reuse', message='复用已加载模型和参考声音缓存')
    return MODEL


def generate(spec, progress):
    model = get_model(spec, progress)
    import numpy as np
    import torch
    import soundfile as sf
    seed = int(spec['seed'])
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    total_chars = max(1, sum(len(s['text']) for s in spec['segments']))
    completed_chars = 0
    for position, segment in enumerate(spec['segments']):
        number = segment.get('index', position) + 1
        total = spec.get('total_segments', len(spec['segments']))
        def on_progress(value, desc='', **kwargs):
            if value < 0.1:
                label = '分析参考声音'
            elif value < 0.2:
                label = '处理目标文字'
            elif value < 0.9:
                label = '合成语音'
            else:
                label = '保存音频'
            progress.emit(phase='generating', fraction=(completed_chars + len(segment['text']) * value) / total_chars,
                          segment_index=number - 1, message=f'片段 {number}/{total}：{label}')
        model.gr_progress = on_progress
        on_progress(0)
        segment_started = time.perf_counter()
        model.infer(spk_audio_prompt=spec['reference'], text=segment['text'],
                    output_path=segment['path'], emo_audio_prompt=spec['reference'],
                    emo_alpha=1.0, lang=spec['language'], duration_factor=1.0,
                    num_beams=1, verbose=False)
        progress.emit('metric', metric='synthesis', seconds=time.perf_counter() - segment_started,
                      units=max(1, len(segment['text'])))
        completed_chars += len(segment['text'])
        progress.emit(phase='generating', fraction=completed_chars / total_chars,
                      message=f'已生成片段 {number}/{total}')
    for segment in spec['segments']:
        data, sr = sf.read(segment['path'])
        if len(data) < sr * 0.15 or not np.isfinite(data).all() or np.max(np.abs(data)) < 1e-5:
            raise RuntimeError('模型生成了空白或损坏的音频。')


if __name__ == '__main__':
    main()
