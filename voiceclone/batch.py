"""Ordered independent takes; transcription is informational and never gates audio."""
import hashlib
import queue
import secrets
import shutil
import threading
import time
import uuid

from voiceclone import service as svc
from voiceclone.progress import TERMINAL, snapshot as single_snapshot

TRANSCRIPTIONS = queue.Queue()
TRANSCRIPTION_LOCK = threading.Lock()
TRANSCRIPTION_THREAD = None


def initialize(job):
    seed = secrets.randbelow(2 ** 31 - 3)
    job.update(variant_count=3, current_variant=0, variants=[
        {'id': uuid.uuid4().hex, 'number': number + 1, 'seed': seed + number,
         'state': 'queued', 'generated': False, 'message': '等待生成',
         'transcription_state': 'waiting', 'recognized_text': ''}
        for number in range(3)])


def snapshot(job):
    child = job.get('_active_child')
    if child and job['state'] not in TERMINAL:
        result = single_snapshot(child)
        result['percent'] = round(((job['current_variant'] - 1) * 100 + result.get('percent', 0)) / len(job['variants']))
        result['elapsed_seconds'] = max(0, int(time.time() - job['started_at']))
        return result
    result = single_snapshot(job)
    if job['state'] in ['error', 'cancelled']:
        result['percent'] = round(sum(v['generated'] for v in job['variants']) / len(job['variants']) * 100)
    return result


def publish(parent, child, index):
    # update() already holds the service's reentrant lock.
    result = svc.public(child)
    result.pop('segments', None)
    result.pop('text', None)
    result.pop('reference_text', None)
    if result.get('generated'):
        result.update(preview=f'/audio/variant/{parent["id"]}/{index}',
                      download=f'/download/variant/{parent["id"]}/{index}')
    parent['variants'][index].update(result)
    if parent.get('_active_child') is child and child['state'] not in TERMINAL:
        parent.update(state=child['state'], message=f'第 {index + 1}/3 次：{child["message"]}')
    svc.save(parent)


def transcribe_result(child, path):
    if child['_cancel'].is_set():
        svc.update(child, transcription_state='cancelled')
        return
    svc.update(child, transcription_state='processing')
    try:
        text = svc.transcribe(path, child['language'].lower(), offline=True)
        svc.update(child, transcription_state='ready', recognized_text=text)
    except Exception as exc:
        svc.update(child, transcription_state='error', transcription_error=str(exc))


def transcription_worker():
    while True:
        child, path = TRANSCRIPTIONS.get()
        try:
            transcribe_result(child, path)
        finally:
            TRANSCRIPTIONS.task_done()


def enqueue_transcription(child, path):
    global TRANSCRIPTION_THREAD
    svc.update(child, transcription_state='queued')
    with TRANSCRIPTION_LOCK:
        if TRANSCRIPTION_THREAD is None or not TRANSCRIPTION_THREAD.is_alive():
            TRANSCRIPTION_THREAD = threading.Thread(target=transcription_worker, daemon=True)
            TRANSCRIPTION_THREAD.start()
    TRANSCRIPTIONS.put((child, path))


def audio_file(job, index):
    if index < 0 or index >= len(job.get('variants', [])):
        raise KeyError(index)
    variant = job['variants'][index]
    if not variant.get('generated'):
        raise ValueError('本次音频尚未生成完成。')
    path = svc.DATA / 'jobs' / variant['id'] / 'candidate.wav'
    if hashlib.sha256(path.read_bytes()).hexdigest() != variant['sha256']:
        raise ValueError('音频校验摘要不一致。')
    return path


def run(job):
    job['started_at'] = time.time()
    try:
        for index, variant in enumerate(job['variants']):
            svc.check_cancel(job)
            directory = svc.DATA / 'jobs' / variant['id']
            directory.mkdir()
            child = {**variant, 'engine': job['engine'], 'text': job['text'],
                     'reference_text': job.get('reference_text', ''), 'language': job['language'],
                     'created': time.time(), 'automatic_verification': False,
                     'segments': [{'text': s['text'], 'generated': False} for s in job['segments']],
                     '_reference': job['_reference'], '_cancel': job['_cancel'], '_process': None,
                     '_managed': True, '_profile': job['_profile'],
                     '_on_update': lambda current, i=index: publish(job, current, i)}
            job['_active_child'] = child
            svc.update(job, current_variant=index + 1, state='loading', message=f'第 {index + 1}/3 次：准备生成')
            svc.run_single_job(child)
            svc.check_cancel(job)
            if child.get('generated'):
                if not job['generated']:
                    final = svc.DATA / 'jobs' / job['id'] / 'candidate.wav'
                    shutil.copyfile(directory / 'candidate.wav', final)
                    svc.update(job, generated=True, sha256=child['sha256'],
                               preview=f'/audio/final/{job["id"]}', download=f'/download/{job["id"]}')
                try:
                    enqueue_transcription(child, directory / 'candidate.wav')
                except Exception as exc:
                    svc.update(child, transcription_state='error', transcription_error=str(exc))
            else:
                svc.update(child, transcription_state='unavailable')
        count = sum(v['generated'] for v in job['variants'])
        svc.update(job, state='ready' if count else 'error',
                   message=f'三次生成已结束，{count} 份音频可试听、下载。识别文字仅供自行校对。')
    except InterruptedError:
        svc.update(job, state='cancelled', message='已停止后续生成，已完成的音频仍可试听、下载。')
    except Exception as exc:
        svc.update(job, state='error', message=str(exc))
    finally:
        with svc.LOCK:
            for variant in job['variants']:
                if variant['state'] not in TERMINAL:
                    variant.update(state='cancelled', message='未继续生成', transcription_state='cancelled')
            job['_active_child'] = None
            svc.save(job)
        svc.ACTIVE.release()
