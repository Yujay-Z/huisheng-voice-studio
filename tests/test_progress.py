import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import types

from voiceclone import native_paths, progress


class ProgressTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parent.parent / '.cache/test-temp'
        base.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.job = {'engine': 'index', 'created': 100, 'started_at': 100,
                    'state': 'loading', 'text': '你好。', 'passed': False,
                    'segments': [{'text': '你好。', 'passed': False}]}

    def test_partial_utf8_event_is_not_discarded(self):
        path = self.folder / 'events.jsonl'
        raw = (json.dumps({'kind': 'progress', 'message': '正在生成'}, ensure_ascii=False) + '\n').encode('utf-8')
        split = raw.index('正'.encode('utf-8')) + 1
        path.write_bytes(raw[:split])
        reader = progress.EventReader(path)
        self.assertEqual(reader.read(), [])
        with path.open('ab') as stream:
            stream.write(raw[split:])
        self.assertEqual(reader.read()[0]['message'], '正在生成')
        self.assertEqual(reader.read(), [])

    def test_overdue_estimate_is_not_a_fake_zero_countdown(self):
        self.job['progress'] = progress.phase_info(self.job, 'loading', now=100)
        self.assertGreater(progress.snapshot(self.job, now=105)['eta_seconds'], 0)
        self.assertIsNone(progress.snapshot(self.job, now=200)['eta_seconds'])

    def test_failure_freezes_elapsed_and_preserves_incomplete_progress(self):
        self.job['state'] = 'generating'
        self.job['progress'] = progress.phase_info(self.job, 'generating', .4, now=110)
        self.job.update(state='error', finished_at=125)
        info = progress.snapshot(self.job, now=800)
        self.assertEqual(info['elapsed_seconds'], 25)
        self.assertLess(info['percent'], 100)
        self.assertIsNone(info['eta_seconds'])
        self.assertFalse(info['indeterminate'])

    def test_progress_callbacks_do_not_restart_stage_timer(self):
        self.job['progress'] = progress.phase_info(self.job, 'generating', .1, now=110)
        later = progress.phase_info(self.job, 'generating', .5, now=120)
        self.assertEqual(later['stage_started_at'], 110)

    def test_measurements_survive_restart_and_change_estimates(self):
        progress.record_metric(self.folder, 'index', 'synthesis', 4, 20)
        profile = progress.read_profile(self.folder, 'index')
        self.assertEqual(profile['synthesis']['value'], .2)
        self.job['_profile'] = profile
        info = progress.phase_info(self.job, 'generating', now=100)
        self.assertEqual(info['estimate_source'], '含本机实测')
        self.assertAlmostEqual(info['expected_stage_seconds'], len(self.job['text']) * .2)

    def test_fast_estimate_has_no_verification_budget(self):
        self.job['_profile'] = {'synthesis': {'value': 2}, 'verification': {'value': 999}}
        info = progress.phase_info(self.job, 'generating', now=100)
        self.assertEqual(info['expected_stage_seconds'], 6)
        self.assertEqual(info['remaining_after_stage'], 1)
        self.job.update(state='ready', generated=True, finished_at=108, progress=info)
        self.assertEqual(progress.snapshot(self.job, now=900)['percent'], 100)
        self.assertEqual(progress.snapshot(self.job, now=900)['elapsed_seconds'], 8)


class NativePathTests(unittest.TestCase):
    def test_loader_restored_even_when_wetext_import_fails(self):
        original = Mock()
        fake = types.SimpleNamespace(TextNormalizer=original)
        with patch.dict('sys.modules', {'kaldifst': fake}), \
             patch.object(native_paths.os, 'name', 'nt'), \
             patch.object(native_paths.importlib, 'import_module', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                native_paths.preload_wetext()
        self.assertIs(fake.TextNormalizer, original)

    def test_import_uses_safe_path_and_returns_real_native_object(self):
        native_object = object()
        original = Mock(return_value=native_object)
        fake = types.SimpleNamespace(TextNormalizer=original)
        def importing(name):
            self.assertEqual(name, 'wetext')
            self.assertIs(fake.TextNormalizer('resource'), native_object)
            return 'loaded'
        with patch.dict('sys.modules', {'kaldifst': fake}), \
             patch.object(native_paths.os, 'name', 'nt'), \
             patch.object(native_paths, 'native_resource_path', return_value='safe/path.fst'), \
             patch.object(native_paths.importlib, 'import_module', side_effect=importing):
            self.assertEqual(native_paths.preload_wetext(), 'loaded')
        original.assert_called_once_with('safe/path.fst')
        self.assertIs(fake.TextNormalizer, original)
