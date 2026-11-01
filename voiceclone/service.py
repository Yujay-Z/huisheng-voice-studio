import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from voiceclone.text import split_text
from voiceclone.resident import WORKER
from voiceclone.runtime import configure_runtime, engine_python
from voiceclone import audio
from voiceclone.models import index_missing_files, find_local_asr
from voiceclone.progress import (EventReader, TERMINAL, phase_info, read_profile,
                                 record_metric, snapshot as progress_snapshot)

ROOT = Path(__file__).resolve().parent.parent
configure_runtime(ROOT)
DATA = ROOT / 'data'
CONFIG = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
for folder in ['uploads', 'jobs']:
    (DATA / folder).mkdir(parents=True, exist_ok=True)
os.environ.setdefault('HF_HOME', str(ROOT / '.cache/huggingface'))
os.environ.setdefault('PYTHONUTF8', '1')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ['PATH'] = str(ROOT / '.tools/ffmpeg') + os.pathsep + os.environ.get('PATH', '')
JOBS = {}
LOCK = threading.RLock()
ASR_LOCK = threading.Lock()
ACTIVE = threading.Lock()
ASR = None
PRELOAD = {'state': 'idle'}


def model_status():
    if WORKER.ready():
        return {'state': 'ready', 'message': '模型已就绪，连续生成无需重新加载'}
    result = dict(PRELOAD)
    if result.get('started_at'):
        result['elapsed_seconds'] = max(0, int(time.time() - result['started_at']))
    if result['state'] == 'ready':
        result.update(state='idle', message='下次生成时重新加载模型')
    return result


def start_preload():
    """Prepare weights while the user uploads/edits; never synthesize on startup."""
    if not CONFIG.get('preload_on_start', True) or not readiness()['index']['ready']:
        return
    directory = ROOT / '.cache' / 'warmup' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    result_path = directory / 'result.json'
    request = directory / 'request.json'
    request.write_text(json.dumps({'engine': 'index', 'repo': str(ROOT / 'engines/index'),
                                   'warmup': True, 'result_path': str(result_path),
                                   'progress_path': str(directory / 'progress.jsonl')}), encoding='utf-8')
    PRELOAD.update(state='loading', started_at=time.time(), message='正在准备模型，可先上传、编辑音频')
    try:
        process, _ = WORKER.submit(engine_python(ROOT),
                                   ROOT / 'voiceclone/engine_worker.py', request, ROOT)
    except Exception as exc:
        PRELOAD.update(state='error', message=str(exc))
        return
    def monitor():
        try:
            while not result_path.exists():
                if process.poll() is not None:
                    raise RuntimeError('模型准备进程已退出，生成时将重新尝试。')
                time.sleep(.2)
            result = json.loads(result_path.read_text(encoding='utf-8'))
            if not result['ok']:
                raise RuntimeError(result.get('error') or '模型准备失败')
            WORKER.mark_loaded(process)
            PRELOAD.update(state='ready', message='模型已就绪')
        except Exception as exc:
            PRELOAD.update(state='error', message=str(exc))
    threading.Thread(target=monitor, daemon=True).start()


def save(job):
    with LOCK:
        payload = {k: v for k, v in job.items() if not k.startswith('_')}
        path = DATA / 'jobs' / job['id'] / 'report.json'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)


def update(job, **values):
    with LOCK:
        job.update(values)
        if values.get('state') in TERMINAL:
            job.setdefault('finished_at', time.time())
        save(job)
        notify = job.get('_on_update')
        if notify:
            notify(job)


def public(job):
    with LOCK:
        result = json.loads(json.dumps({k: v for k, v in job.items() if not k.startswith('_')}))
        if job.get('variants'):
            from voiceclone.batch import snapshot
            result['progress'] = snapshot(job)
        else:
            result['progress'] = progress_snapshot(job)
        return result


def set_phase(job, phase, fraction=0, message=None):
    values = {'state': phase,
              'progress': phase_info(job, phase, fraction)}
    if message is not None:
        values['message'] = message
    update(job, **values)


def apply_worker_events(job, reader):
    for event in reader.read():
        job['_last_heartbeat'] = event.get('at', time.time())
        if event.get('kind') == 'metric':
            timings = job.setdefault('timings', {})
            timings[event['metric']] = round(timings.get(event['metric'], 0) + event['seconds'], 3)
            record_metric(DATA, job['engine'], event['metric'], event['seconds'], event.get('units', 1))
            job['_profile'] = read_profile(DATA, job['engine'])
        elif event.get('kind') == 'reuse':
            job['model_reused'] = True
        elif event.get('kind') == 'progress' and event.get('phase') in ['loading', 'generating']:
            if event['phase'] == 'generating' and '_generation_started' not in job:
                job['_generation_started'] = time.time()
                job.setdefault('timings', {})['model_wait'] = round(max(0, event.get('at', time.time()) - job['started_at']), 3)
            set_phase(job, event['phase'], event.get('fraction', 0), event.get('message'))


def readiness():
    repo = ROOT / 'engines/index'
    missing = index_missing_files(repo / 'checkpoints')
    ready = engine_python(ROOT).exists() and not missing
    status = '文件已就绪，等待实测' if ready else '未安装完成'
    repair = None
    if missing and all('/bigvgan/' in file for file in missing):
        status = '缺少 BigVGAN，请运行“补下载BigVGAN.cmd”'
        repair = '补下载BigVGAN.cmd'
    return {'index': {'name': 'IndexTTS-2.5', 'ready': bool(ready), 'status': status,
                      'missing': missing, 'repair': repair}}


def upload_path(file_id):
    if not isinstance(file_id, str) or not __import__('re').fullmatch(r'[a-f0-9]{32}', file_id):
        raise ValueError('无效音频编号。')
    path = DATA / 'uploads' / (file_id + '.wav')
    if not path.exists():
        raise ValueError('参考音频不存在，请重新上传。')
    return path


def prepare_upload(raw, suffix):
    import imageio_ffmpeg
    ident = uuid.uuid4().hex
    source = DATA / 'uploads' / (ident + suffix)
    output = DATA / 'uploads' / (ident + '.wav')
    source.write_bytes(raw)
    try:
        proc = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y',
                               '-i', str(source), '-t', '601', '-ac', '2', '-ar', '44100',
                               '-c:a', 'pcm_s16le', str(output)], capture_output=True, timeout=60,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if proc.returncode:
            raise ValueError('无法读取音频，请使用有效的 WAV、MP3、FLAC 或 M4A 文件。')
        metadata = audio.describe(output, ident)
        if not 1 <= metadata['seconds'] <= 600:
            raise ValueError('上传音频需为 1 秒至 10 分钟；生成前可裁剪为 1–30 秒。')
        if metadata['peak_amplitude'] < 0.002:
            raise ValueError('参考音频音量过低或无有效声音。')
        return metadata
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        # Converted wav has a distinct name; source never replaces an existing user file.
        if source != output:
            source.unlink(missing_ok=True)


def transcribe(path, language='zh', offline=False):
    global ASR
    from faster_whisper import WhisperModel
    with ASR_LOCK:
        if ASR is None:
            local_model = find_local_asr(ROOT, CONFIG['asr_model'])
            if offline and local_model is None:
                raise RuntimeError('本地识别模型未安装完整，请运行“补下载IndexTTS模型.cmd”。')
            ASR = WhisperModel(str(local_model) if local_model else CONFIG['asr_model'], device='cpu', compute_type='int8',
                               download_root=str(ROOT / 'models/asr'), local_files_only=offline, cpu_threads=2, num_workers=1)
        segments, _ = ASR.transcribe(str(path), language=language or None, beam_size=5,
                                     vad_filter=False, condition_on_previous_text=False,
                                     initial_prompt='以下是普通话的简体中文转写。' if language == 'zh' else None,
                                     temperature=0.0)
        return ''.join(segment.text for segment in segments).strip()


def combine(paths, dest):
    import numpy as np
    import soundfile as sf
    pieces, rate = [], None
    for path in paths:
        wave, sr = sf.read(path, dtype='float32', always_2d=True)
        if rate is not None and sr != rate:
            raise RuntimeError('生成片段采样率不同，无法拼接。')
        rate = sr
        pieces.append(wave.mean(axis=1))
        pieces.append(np.zeros(int(sr * 0.16), dtype='float32'))
    sf.write(dest, np.concatenate(pieces[:-1]), rate, subtype='PCM_16')


def create_job(payload):
    engine = payload.get('engine', 'index')
    if engine != 'index':
        raise ValueError('当前仅支持 IndexTTS-2.5。')
    engine_state = readiness()[engine]
    if not engine_state['ready']:
        raise ValueError(engine_state['status'] + '。请先补齐模型文件。')
    reference = upload_path(payload.get('reference'))
    import soundfile as sf
    reference_info = sf.info(reference)
    if not 1 <= reference_info.duration <= 30:
        raise ValueError('生成前请先将参考音频裁剪为 1–30 秒。')
    text = str(payload.get('text', '')).strip()
    if len(text) > 3000:
        raise ValueError('单次最多 3000 字，请拆分任务。')
    segments = split_text(text, CONFIG['max_segment_chars'], pack=True)
    ref_text = str(payload.get('reference_text', '')).strip()
    if len(ref_text) > 1500 or any(c in ref_text for c in '<>[]{}'):
        raise ValueError('参考原文过长或包含控制标记。')
    language = payload.get('language', 'ZH')
    if language not in ['ZH', 'EN']:
        raise ValueError('当前支持中文和英文。')
    if not ACTIVE.acquire(blocking=False):
        raise ValueError('已有任务运行中，请等待结束或取消。')
    ident = uuid.uuid4().hex
    directory = DATA / 'jobs' / ident
    directory.mkdir()
    job = {'id': ident, 'engine': engine, 'text': text, 'reference_text': ref_text,
           'language': language, 'created': time.time(), 'state': 'queued',
           'message': '准备生成', 'generated': False, 'automatic_verification': False,
           'segments': [{'text': s, 'generated': False} for s in segments],
           '_cancel': threading.Event(), '_reference': str(reference), '_process': None}
    job['_profile'] = read_profile(DATA, engine)
    from voiceclone.batch import initialize
    initialize(job)
    with LOCK:
        JOBS[ident] = job
        save(job)
        # Capture the queued response before the worker can mutate the job.
        # The UI must receive a valid task even before the first progress event.
        initial_response = public(job)
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return initial_response


def check_cancel(job):
    if job['_cancel'].is_set():
        raise InterruptedError('任务已取消。')


def run_job(job):
    if job.get('variants'):
        from voiceclone.batch import run
        return run(job)
    return run_single_job(job)


def run_single_job(job):
    directory = DATA / 'jobs' / job['id']
    job.setdefault('started_at', time.time())
    job.setdefault('_profile', read_profile(DATA, job['engine']))
    try:
        check_cancel(job)
        set_phase(job, 'loading', message='准备模型')
        events_path = directory / 'progress.jsonl'
        result_path = directory / 'result.json'
        reader = EventReader(events_path)
        spec = {'engine': job['engine'], 'repo': str(ROOT / 'engines' / job['engine']),
                'reference': job['_reference'],
                'language': job['language'], 'seed': job.get('seed', 2027),
                'progress_path': str(events_path), 'result_path': str(result_path),
                'total_segments': len(job['segments']),
                'segments': [{'text': s['text'], 'index': i, 'path': str(directory / f'{i}.wav')}
                             for i, s in enumerate(job['segments'])]}
        spec_path = directory / 'request.json'
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
        py = engine_python(ROOT)
        proc, reused = WORKER.submit(py, ROOT / 'voiceclone/engine_worker.py', spec_path, ROOT)
        job['_process'] = proc
        job['model_reused'] = reused
        if reused and WORKER.ready():
            set_phase(job, 'generating', message='复用已加载模型，开始生成')
        start = time.monotonic()
        while not result_path.exists():
            apply_worker_events(job, reader)
            if proc.poll() is not None and not result_path.exists():
                raise RuntimeError('模型进程意外退出，请查看任务日志或 logs/engine-worker.log。')
            if job['_cancel'].wait(0.2) or time.monotonic() - start > 1800:
                WORKER.close()
                check_cancel(job)
                raise RuntimeError('模型推理超过 30 分钟，已停止。')
        apply_worker_events(job, reader)
        check_cancel(job)
        result = json.loads(result_path.read_text(encoding='utf-8'))
        if not result['ok']:
            log_path = directory / 'engine.log'
            detail = log_path.read_text(encoding='utf-8', errors='replace')[-1800:] if log_path.exists() else result.get('error')
            raise RuntimeError('模型运行失败，请查看任务日志。\n' + str(detail))
        WORKER.mark_loaded(proc)
        for i, segment in enumerate(job['segments']):
            segment.update(generated=True, preview=f'/audio/segment/{job["id"]}/{i}')
        set_phase(job, 'combining', message='正在保存完整音频')
        final = directory / 'candidate.wav'
        if len(job['segments']) == 1:
            import shutil
            shutil.copyfile(directory / '0.wav', final)
        else:
            combine([directory / f'{i}.wav' for i in range(len(job['segments']))], final)
        check_cancel(job)
        update(job, state='ready', generated=True,
               sha256=hashlib.sha256(final.read_bytes()).hexdigest(),
               preview=f'/audio/final/{job["id"]}', download=f'/download/{job["id"]}',
               message='生成完成，可直接试听、导出。内容、读音和情感请自行核查。')
    except InterruptedError:
        WORKER.close()
        update(job, state='cancelled', message='任务已取消。')
    except Exception as exc:
        WORKER.close()
        update(job, state='error', message=str(exc))
    finally:
        job['_process'] = None
        if not job.get('_managed'):
            ACTIVE.release()


def approve(job):
    with LOCK:
        if job['state'] != 'ready' or not job.get('generated'):
            raise ValueError('音频尚未生成完成。')
        path = DATA / 'jobs' / job['id'] / 'candidate.wav'
        if hashlib.sha256(path.read_bytes()).hexdigest() != job['sha256']:
            raise ValueError('音频文件发生变化，请重新生成。')
        return {'url': '/download/' + job['id']}
