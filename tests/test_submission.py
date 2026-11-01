from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import wave
import unittest
from unittest.mock import patch

from app import Handler, TOKEN
from voiceclone import service, progress


class SubmissionTests(unittest.TestCase):
    def test_queued_job_without_worker_progress_has_a_valid_snapshot(self):
        job = {'state': 'queued', 'created': 100, 'segments': [{'text': '你好'}], 'text': '你好'}
        snapshot = progress.snapshot(job, now=101)
        self.assertEqual(snapshot['elapsed_seconds'], 1)
        self.assertEqual(snapshot['percent'], 0)
        self.assertTrue(snapshot['indeterminate'])

    def test_real_post_returns_job_before_any_worker_progress_exists(self):
        base = service.ROOT / '.cache/test-temp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            data = Path(temporary)
            (data / 'jobs').mkdir()
            with wave.open(str(data / 'reference.wav'), 'wb') as stream:
                stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0\0' * 24000)
            release, started, ended = threading.Event(), threading.Event(), threading.Event()
            active = threading.Lock()
            def held_worker(job):
                started.set()
                try:
                    release.wait(5)
                finally:
                    active.release()
                    ended.set()
            with patch.object(service, 'DATA', data), patch.object(service, 'JOBS', {}), \
                 patch.object(service, 'ACTIVE', active), \
                 patch.object(service, 'readiness', return_value={'index': {'ready': True}}), \
                 patch.object(service, 'upload_path', return_value=data / 'reference.wav'), \
                 patch.object(service, 'run_job', side_effect=held_worker):
                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                server.daemon_threads = True
                server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                try:
                    connection.request('POST', '/api/jobs', json.dumps({'reference': 'test', 'text': '你好'}),
                                       {'Content-Type': 'application/json', 'X-Local-Token': TOKEN})
                    response = connection.getresponse()
                    body = json.loads(response.read())
                    self.assertEqual(response.status, 200, body)
                    self.assertTrue(body['id'])
                    self.assertEqual(body['state'], 'queued')
                    self.assertEqual(body['engine'], 'index')
                    self.assertEqual(len(body['variants']), 3)
                    self.assertEqual(len({v['seed'] for v in body['variants']}), 3)
                    self.assertFalse(body['automatic_verification'])
                    self.assertEqual(body['progress']['percent'], 0)
                    self.assertTrue(started.wait(1))
                finally:
                    connection.close()
                    release.set()
                    server.shutdown()
                    server.server_close()
                    server_thread.join(timeout=3)
                    if started.is_set():
                        self.assertTrue(ended.wait(2))
