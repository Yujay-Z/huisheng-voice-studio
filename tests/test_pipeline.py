import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from voiceclone.text import compare, normalize, split_text
from voiceclone import service


class TextTests(unittest.TestCase):
    def test_empty_does_not_pass(self):
        self.assertFalse(compare('，。', '')['passed'])

    def test_allowed_normalization_only(self):
        self.assertTrue(compare('你好，ＡＢＣ！', '你好 abc')['passed'])
        for a, b in [('128元', '一百二十八元'), ('再见', '在见'), ('我今天回家', '我回家'), ('汉字', '漢字')]:
            self.assertFalse(compare(a, b)['passed'])

    def test_split_keeps_every_spoken_character(self):
        text = '第一句话。\n第二句话！' + '非常长的句子，' * 40 + '结束。'
        segments = split_text(text)
        self.assertEqual(normalize(text), normalize(''.join(segments)))
        self.assertTrue(all(len(s) <= 72 for s in segments))
        with self.assertRaises(ValueError):
            split_text('你好<|endofprompt|>')

    def test_fast_packing_reduces_model_calls_without_losing_text(self):
        text = '你好。再见！' * 30
        packed = split_text(text, 72, pack=True)
        self.assertLess(len(packed), len(split_text(text)))
        self.assertEqual(normalize(''.join(packed)), normalize(text))
        self.assertTrue(all(len(part) <= 72 for part in packed))


class PipelineTests(unittest.TestCase):
    def test_generation_readiness_does_not_require_asr_model(self):
        with patch.object(Path, 'exists', return_value=True), \
             patch.object(service, 'index_missing_files', return_value=[]), \
             patch.object(service, 'find_local_asr', return_value=None) as asr:
            self.assertTrue(service.readiness()['index']['ready'])
            self.assertEqual(set(service.readiness()), {'index'})
            asr.assert_not_called()

    def test_other_engine_is_rejected_before_starting_a_job(self):
        with self.assertRaisesRegex(ValueError, '仅支持 IndexTTS-2.5'):
            service.create_job({'engine': 'unsupported'})

    def run_pipeline(self, fail=False, cancelled=False, single=False, reused=False, cancel_running=False):
        temp_root = service.ROOT / '.cache/test-temp'
        temp_root.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        ident = 'a' * 32
        (root / 'jobs' / ident).mkdir(parents=True)
        job = {'id': ident, 'engine': 'index', 'text': '你好。再见。', 'reference_text': '参考',
               'language': 'ZH', 'generated': False, 'state': 'queued',
               'segments': [{'text': text, 'generated': False} for text in (['你好。'] if single else ['你好。', '再见。'])],
               '_reference': 'reference.wav', '_cancel': threading.Event(), '_process': None}
        if cancelled:
            job['_cancel'].set()
        worker = Mock()
        def submit(python, script, request, project):
            spec = json.loads(request.read_text(encoding='utf-8'))
            if cancel_running:
                job['_cancel'].set()
                return Mock(poll=Mock(return_value=None)), reused
            for s in spec['segments']:
                Path(s['path']).write_bytes(b'simulated audio')
            events = [{'kind': 'metric', 'at': time.time(), 'metric': 'synthesis', 'seconds': 1, 'units': 6},
                      {'kind': 'progress', 'at': time.time(), 'phase': 'generating', 'fraction': 1}]
            Path(spec['progress_path']).write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
            Path(spec['result_path']).write_text(json.dumps({'ok': not fail, 'error': 'model failed' if fail else None}), encoding='utf-8')
            return Mock(poll=Mock(return_value=None)), reused
        worker.submit.side_effect = submit
        lock = threading.Lock()
        lock.acquire()
        def fake_combine(paths, dest):
            dest.write_bytes(b'joined audio')
        with patch.object(service, 'DATA', root), patch.object(service, 'ACTIVE', lock), \
             patch.object(service, 'WORKER', worker), \
             patch.object(service, 'transcribe', side_effect=AssertionError('生成流程不应识别音频')) as asr, \
             patch.object(service, 'combine', fake_combine):
            service.run_job(job)
            self.assertFalse(lock.locked())
            asr.assert_not_called()
            return job, root, worker

    def test_generate_once_without_asr_or_approval_gate(self):
        job, root, worker = self.run_pipeline()
        worker.submit.assert_called_once()
        worker.close.assert_not_called()
        self.assertTrue(job['generated'])
        self.assertNotIn('final_verification', job)
        self.assertNotIn('passed', job)
        self.assertEqual(job['state'], 'ready')
        self.assertEqual((root / 'jobs' / job['id'] / 'candidate.wav').read_bytes(), b'joined audio')
        self.assertIn('_last_heartbeat', job)
        self.assertIn('synthesis', job['_profile'])
        self.assertEqual(service.public(job)['progress']['percent'], 100)
        with patch.object(service, 'DATA', root):
            service.approve(job)
            (root / 'jobs' / job['id'] / 'candidate.wav').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                service.approve(job)

    def test_model_error_is_not_retried_and_discards_resident(self):
        job, root, worker = self.run_pipeline(fail=True)
        worker.submit.assert_called_once()
        worker.close.assert_called_once()
        self.assertFalse(job['generated'])
        self.assertEqual(job['state'], 'error')
        with self.assertRaises(ValueError):
            service.approve(job)

    def test_single_segment_is_copied_without_reencoding(self):
        job, root, worker = self.run_pipeline(single=True, reused=True)
        self.assertEqual(job['state'], 'ready')
        self.assertTrue(job['model_reused'])
        self.assertEqual((root / 'jobs' / job['id'] / 'candidate.wav').read_bytes(), b'simulated audio')

    def test_cancelled_job_does_not_start_model_and_releases_lock(self):
        job, root, worker = self.run_pipeline(cancelled=True)
        worker.submit.assert_not_called()
        worker.close.assert_called_once()
        self.assertFalse(job['generated'])
        self.assertEqual(job['state'], 'cancelled')

    def test_cancel_running_task_closes_worker_without_publishing_audio(self):
        job, root, worker = self.run_pipeline(cancel_running=True)
        worker.submit.assert_called_once()
        self.assertTrue(worker.close.called)
        self.assertEqual(job['state'], 'cancelled')
        self.assertNotIn('download', job)


if __name__ == '__main__':
    unittest.main()
