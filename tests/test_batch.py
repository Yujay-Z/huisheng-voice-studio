from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from voiceclone import batch, service


class BatchTests(unittest.TestCase):
    @contextmanager
    def pipeline(self, fail=0, cancel=0):
        base = service.ROOT / '.cache/test-temp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            data = Path(folder)
            ident = 'c' * 32
            (data / 'jobs' / ident).mkdir(parents=True)
            job = {'id': ident, 'engine': 'index', 'text': '你好。', 'language': 'ZH',
                   'state': 'queued', 'generated': False, 'automatic_verification': False,
                   'segments': [{'text': '你好。', 'generated': False}],
                   '_cancel': threading.Event(), '_reference': 'reference.wav', '_profile': {}}
            batch.initialize(job)
            lock = threading.Lock()
            lock.acquire()
            worker, specs, pending = Mock(), [], []
            def submit(python, script, request, root):
                self.assertTrue(lock.locked(), 'the synthesis slot must stay reserved for all three takes')
                if specs:
                    first = batch.audio_file(job, 0)
                    self.assertTrue(first.is_file(), 'the first take must be available before later takes finish')
                spec = json.loads(request.read_text(encoding='utf-8'))
                specs.append(spec)
                number = len(specs)
                for segment in spec['segments']:
                    Path(segment['path']).write_bytes(f'audio seed={spec["seed"]}'.encode())
                Path(spec['result_path']).write_text(json.dumps({'ok': number != fail, 'error': 'test failure'}), encoding='utf-8')
                if number == cancel:
                    job['_cancel'].set()
                return Mock(poll=Mock(return_value=None)), number > 1
            worker.submit.side_effect = submit
            def enqueue(child, path):
                service.update(child, transcription_state='queued')
                pending.append((child, path))
            with patch.object(service, 'DATA', data), patch.object(service, 'ACTIVE', lock), \
                 patch.object(service, 'WORKER', worker), patch.object(batch, 'enqueue_transcription', side_effect=enqueue), \
                 patch.object(service, 'transcribe') as asr:
                service.run_job(job)
                self.assertFalse(lock.locked())
                asr.assert_not_called()
                yield job, worker, specs, pending, data

    def test_three_ordered_independent_runs_do_not_wait_for_transcription(self):
        with self.pipeline() as (job, worker, specs, pending, data):
            self.assertEqual(worker.submit.call_count, 3)
            self.assertEqual(len({spec['seed'] for spec in specs}), 3)
            self.assertEqual([v['number'] for v in job['variants']], [1, 2, 3])
            self.assertEqual(len(pending), 3)
            self.assertEqual(job['state'], 'ready')
            self.assertTrue(all(v['transcription_state'] == 'queued' for v in job['variants']))
            self.assertEqual(len({batch.audio_file(job, i).read_bytes() for i in range(3)}), 3)
            self.assertEqual(service.public(job)['progress']['percent'], 100)
            with patch.object(service, 'transcribe', return_value='故意不同的识别文字。') as asr:
                batch.transcribe_result(*pending[0])
            asr.assert_called_once_with(pending[0][1], 'zh', offline=True)
            self.assertEqual(job['variants'][0]['recognized_text'], '故意不同的识别文字。')
            self.assertNotIn('passed', job['variants'][0])
            self.assertFalse(job['automatic_verification'])
            self.assertEqual(worker.submit.call_count, 3, 'transcription must never trigger regeneration')
            self.assertTrue((data / 'jobs' / job['id'] / 'candidate.wav').is_file())

    def test_second_run_error_is_shown_and_third_run_is_still_attempted_once(self):
        with self.pipeline(fail=2) as (job, worker, specs, pending, data):
            self.assertEqual(worker.submit.call_count, 3)
            self.assertEqual([v['state'] for v in job['variants']], ['ready', 'error', 'ready'])
            self.assertEqual(len(pending), 2)
            self.assertEqual(job['variants'][1]['transcription_state'], 'unavailable')
            self.assertIn('test failure', job['variants'][1]['message'])
            self.assertTrue(batch.audio_file(job, 2).is_file())

    def test_cancel_preserves_first_audio_and_skips_third_run(self):
        with self.pipeline(cancel=2) as (job, worker, specs, pending, data):
            self.assertEqual(worker.submit.call_count, 2)
            self.assertEqual(job['state'], 'cancelled')
            self.assertEqual([v['state'] for v in job['variants']], ['ready', 'cancelled', 'cancelled'])
            self.assertTrue(batch.audio_file(job, 0).is_file())
            self.assertEqual(service.public(job)['progress']['percent'], 33)
            batch.transcribe_result(*pending[0])
            self.assertEqual(job['variants'][0]['transcription_state'], 'cancelled')

    def test_transcription_error_never_disables_audio_or_changes_generation_state(self):
        with self.pipeline() as (job, worker, specs, pending, data):
            with patch.object(service, 'transcribe', side_effect=RuntimeError('本地识别模型缺失')):
                batch.transcribe_result(*pending[0])
            self.assertEqual(job['state'], 'ready')
            self.assertTrue(job['variants'][0]['generated'])
            self.assertEqual(job['variants'][0]['transcription_state'], 'error')
            self.assertIn('本地识别模型缺失', job['variants'][0]['transcription_error'])
            self.assertTrue(batch.audio_file(job, 0).is_file())


if __name__ == '__main__':
    unittest.main()
