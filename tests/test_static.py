from http.client import HTTPConnection
import json
from http.server import ThreadingHTTPServer
import mimetypes
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import patch

from app import Handler
from voiceclone import service


class StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, path):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode('utf-8')
        finally:
            connection.close()

    def test_asset_mime_types_ignore_windows_registry(self):
        with patch.object(mimetypes, 'guess_type', return_value=('application/octet-stream', None)):
            for path, expected in [('/', 'text/html'), ('/style.css?v=2', 'text/css'), ('/app.js?v=2', 'text/javascript'), ('/results.js?v=2', 'text/javascript')]:
                with self.subTest(path=path):
                    code, headers, body = self.request(path)
                    self.assertEqual(code, 200)
                    self.assertEqual(headers['Content-Type'], expected + '; charset=utf-8')
                    self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                    self.assertTrue(body)

    def test_file_preview_can_resolve_stylesheet_and_script(self):
        import re
        root = Path(__file__).resolve().parent.parent / 'web'
        html = (root / 'index.html').read_text(encoding='utf-8')
        for pattern in [r'<link[^>]+href="([^"]+)"', r'<script[^>]+src="([^"]+)"']:
            reference = re.search(pattern, html).group(1).split('?')[0]
            self.assertFalse(reference.startswith('/'))
            self.assertTrue((root / reference).is_file())
        self.assertIn('id="file-notice"', html)

    def test_state_identifies_current_backend_version(self):
        code, headers, body = self.request('/api/state')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['api_version'], 3)
        self.assertEqual(json.loads(body)['build'], 'editor-variants-v1')
        self.assertEqual(set(json.loads(body)['engines']), {'index'})

    def test_generated_audio_download_needs_no_asr_verdict_or_listening_approval(self):
        import hashlib
        base = service.ROOT / '.cache/test-temp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            data, ident = Path(folder), 'a' * 32
            directory = data / 'jobs' / ident
            directory.mkdir(parents=True)
            raw = b'simulated WAV'
            (directory / 'candidate.wav').write_bytes(raw)
            job = {'state': 'ready', 'generated': True, 'sha256': hashlib.sha256(raw).hexdigest()}
            with patch.object(service, 'DATA', data), patch.object(service, 'JOBS', {ident: job}):
                code, headers, body = self.request('/download/' + ident)
                self.assertEqual(code, 200)
                self.assertEqual(body.encode('utf-8'), raw)
                self.assertIn('attachment', headers['Content-Disposition'])
                job['state'] = 'generating'
                self.assertEqual(self.request('/download/' + ident)[0], 403)

    def test_reference_stems_have_distinct_attachment_names(self):
        base = service.ROOT / '.cache/test-temp'
        with tempfile.TemporaryDirectory(dir=base) as folder:
            data, ident = Path(folder), 'b' * 32
            (data / 'uploads').mkdir()
            (data / 'uploads' / (ident + '.wav')).write_bytes(b'RIFF test audio')
            with patch.object(service, 'DATA', data):
                for stem in ['vocals', 'background']:
                    code, headers, _ = self.request('/audio/reference/' + ident + '?download=' + stem)
                    self.assertEqual(code, 200)
                    self.assertIn('filename="' + stem + '.wav"', headers['Content-Disposition'])

    def test_individual_take_download_is_available_while_batch_and_transcription_are_pending(self):
        import hashlib
        base = service.ROOT / '.cache/test-temp'
        with tempfile.TemporaryDirectory(dir=base) as folder:
            data, ident, child = Path(folder), 'c' * 32, 'd' * 32
            (data / 'jobs' / child).mkdir(parents=True)
            raw = b'simulated take audio'
            path = data / 'jobs' / child / 'candidate.wav'
            path.write_bytes(raw)
            job = {'state': 'generating', 'variants': [
                {'id': child, 'generated': True, 'sha256': hashlib.sha256(raw).hexdigest(), 'transcription_state': 'queued'},
                {'id': 'e' * 32, 'generated': False}]}
            with patch.object(service, 'DATA', data), patch.object(service, 'JOBS', {ident: job}):
                code, headers, body = self.request('/download/variant/' + ident + '/0')
                self.assertEqual(code, 200)
                self.assertIn('voice-1.wav', headers['Content-Disposition'])
                self.assertEqual(body.encode(), raw)
                self.assertEqual(self.request('/download/variant/' + ident + '/1')[0], 403)
                job['state'] = 'cancelled'
                self.assertEqual(self.request('/audio/variant/' + ident + '/0')[0], 200)
                self.assertEqual(self.request('/audio/variant/' + ident + '/2')[0], 404)
                path.write_bytes(b'changed')
                self.assertEqual(self.request('/download/variant/' + ident + '/0')[0], 403)


if __name__ == '__main__':
    unittest.main()
