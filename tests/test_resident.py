import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from voiceclone import engine_worker
from voiceclone.resident import ResidentWorker


class ResidentTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parent.parent / '.cache/test-temp'
        base.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=base)
        def cleanup():
            # Windows may signal process exit just before inherited file handles close.
            for attempt in range(20):
                try:
                    temp.cleanup()
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    threading.Event().wait(.05)
        self.addCleanup(cleanup)
        self.root = Path(temp.name)

    def test_two_real_requests_reuse_process_and_close_allows_restart(self):
        script = self.root / 'stub.py'
        script.write_text('import sys, pathlib, os\nfor line in sys.stdin:\n pathlib.Path(line.strip()).write_text(str(os.getpid()))\n', encoding='utf-8')
        worker = ResidentWorker()
        self.addCleanup(worker.close)
        def send(name):
            output = self.root / name
            proc, reused = worker.submit(sys.executable, script, output, self.root)
            deadline = time.monotonic() + 5
            while not output.exists() and time.monotonic() < deadline:
                threading.Event().wait(.01)
            self.assertTrue(output.exists())
            return proc, reused, output.read_text()
        first, reused, pid = send('one')
        self.assertFalse(reused)
        self.assertFalse(worker.ready())
        worker.mark_loaded(first)
        self.assertTrue(worker.ready())
        second, reused, pid2 = send('two')
        self.assertIs(first, second)
        self.assertTrue(reused)
        self.assertEqual(pid, pid2)
        worker.close()
        self.assertFalse(worker.ready())
        self.assertIsNotNone(first.poll())
        third, reused, _ = send('three')
        self.assertFalse(reused)
        self.assertIsNot(third, first)
        worker.close()
        self.assertIsNotNone(third.poll())

    def test_cached_model_is_loaded_only_once_and_warm_tasks_do_not_change_loading_metric(self):
        spec = {'engine': 'index', 'repo': 'test'}
        model, events = object(), Mock()
        with patch.object(engine_worker, 'MODEL', None), patch.object(engine_worker, 'MODEL_KEY', None), \
             patch.object(engine_worker, 'load_model', return_value=model) as load:
            self.assertIs(engine_worker.get_model(spec, events), model)
            self.assertIs(engine_worker.get_model(spec, events), model)
            load.assert_called_once_with(spec)
        metrics = [c for c in events.emit.call_args_list if c.args == ('metric',)]
        self.assertEqual(len(metrics), 1)
        self.assertEqual(events.emit.call_args.args, ('reuse',))

    def test_request_writes_failure_marker_and_task_log(self):
        spec = {'progress_path': str(self.root / 'events.jsonl'), 'result_path': str(self.root / 'result.json')}
        request = self.root / 'request.json'
        request.write_text(json.dumps(spec), encoding='utf-8')
        with patch.object(engine_worker, 'generate', side_effect=RuntimeError('test model failure')):
            self.assertFalse(engine_worker.run_request(request))
        self.assertFalse(json.loads((self.root / 'result.json').read_text(encoding='utf-8'))['ok'])
        self.assertIn('test model failure', (self.root / 'engine.log').read_text(encoding='utf-8'))

    def test_startup_preparation_loads_weights_without_synthesizing(self):
        spec = {'warmup': True, 'repo': 'test', 'result_path': str(self.root / 'result.json')}
        request = self.root / 'request.json'
        request.write_text(json.dumps(spec), encoding='utf-8')
        with patch.object(engine_worker, 'get_model') as load, patch.object(engine_worker, 'generate') as synthesize:
            self.assertTrue(engine_worker.run_request(request))
        load.assert_called_once()
        synthesize.assert_not_called()
