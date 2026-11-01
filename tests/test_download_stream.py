import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
import download_models
from voiceclone.downloads import download_file


class Response:
    def __init__(self, chunks, status=200, headers=None):
        self.chunks, self.status_code = chunks, status
        self.headers = headers or {'Content-Type': 'application/octet-stream'}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def raise_for_status(self):
        pass
    def iter_content(self, chunk_size):
        for item in self.chunks:
            if isinstance(item, Exception):
                raise item
            yield item


class StreamTests(unittest.TestCase):
    def setUp(self):
        base = download_models.ROOT / '.cache/test-temp'
        base.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_retry_resumes_after_read_timeout(self):
        session = Mock()
        session.get.side_effect = [
            Response([b'abc', requests.ReadTimeout('stalled')]),
            Response([b'def'], 206, {'Content-Range': 'bytes 3-5/6'}),
        ]
        target = self.folder / 'model.bin'
        download_file('https://example.test/model', target, 6,
                      hashlib.sha256(b'abcdef').hexdigest(), session=session)
        self.assertEqual(target.read_bytes(), b'abcdef')
        self.assertEqual(session.get.call_args_list[1].kwargs['headers'], {'Range': 'bytes=3-5'})
        self.assertEqual(session.get.call_args_list[0].kwargs['timeout'], (5, 20))

    def test_ignored_range_restarts_only_incomplete_file(self):
        target = self.folder / 'model.bin'
        target.with_name('model.bin.part').write_bytes(b'abc')
        session = Mock()
        session.get.return_value = Response([b'abcdef'])
        download_file('https://example.test/model', target, 6,
                      hashlib.sha256(b'abcdef').hexdigest(), session=session)
        self.assertEqual(target.read_bytes(), b'abcdef')

    def test_timeout_keeps_partial_for_next_run(self):
        target = self.folder / 'model.bin'
        session = Mock()
        session.get.return_value = Response([b'abc', requests.ReadTimeout('stalled')])
        with self.assertRaisesRegex(RuntimeError, '已保留片段'):
            download_file('https://example.test/model', target, 6,
                          hashlib.sha256(b'abcdef').hexdigest(), session=session, attempts=1)
        self.assertFalse(target.exists())
        self.assertEqual(target.with_name('model.bin.part').read_bytes(), b'abc')

    def test_verified_file_skips_network(self):
        target = self.folder / 'model.bin'
        target.write_bytes(b'abcdef')
        session = Mock()
        download_file('https://example.test/model', target, 6,
                      hashlib.sha256(b'abcdef').hexdigest(), session=session)
        session.get.assert_not_called()

    def test_verified_repository_skips_sdk_download(self):
        target = self.folder / 'weights.bin'
        target.write_bytes(b'abcdef')
        meta = [{'path': 'weights.bin', 'size': 6, 'sha256': hashlib.sha256(b'abcdef').hexdigest()}]
        with patch.object(download_models, 'remote_files', return_value=meta), \
             patch('huggingface_hub.snapshot_download') as downloader:
            download_models.snapshot('huggingface', 'test/model', self.folder, {'weights.bin': 1})
            downloader.assert_not_called()

    def test_multiple_bounded_ranges_are_combined_before_checksum(self):
        target = self.folder / 'model.bin'
        session = Mock()
        session.get.side_effect = [Response([b'abc'], 206, {'Content-Range': 'bytes 0-2/6'}),
                                   Response([b'def'], 206, {'Content-Range': 'bytes 3-5/6'})]
        download_file('https://example.test/model', target, 6,
                      hashlib.sha256(b'abcdef').hexdigest(), session=session, chunk_bytes=3)
        self.assertEqual(target.read_bytes(), b'abcdef')
        self.assertEqual([call.kwargs['headers'] for call in session.get.call_args_list],
                         [{'Range': 'bytes=0-2'}, {'Range': 'bytes=3-5'}])


if __name__ == '__main__':
    unittest.main()
