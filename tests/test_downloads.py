import hashlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import download_models
from voiceclone import models
from voiceclone import service


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temp_root = download_models.ROOT / '.cache/test-temp'
        temp_root.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def test_network_failure_returning_empty_local_directory_does_not_pass(self):
        fake_hub = types.SimpleNamespace(snapshot_download=lambda **kwargs: str(self.directory))
        metadata = [{'path': 'weights.bin', 'size': 20, 'sha256': None}]
        with patch.dict('sys.modules', {'huggingface_hub': fake_hub}), \
             patch.object(download_models, 'remote_files', return_value=metadata):
            with self.assertRaisesRegex(RuntimeError, '缺少'):
                download_models.snapshot('huggingface', 'test/model', self.directory, {'weights.bin': 1})

    def test_same_size_corrupt_weights_fail_checksum(self):
        (self.directory / 'model.bin').write_bytes(b'wrong')
        metadata = [{'path': 'model.bin', 'size': 5,
                     'sha256': hashlib.sha256(b'right').hexdigest()}]
        with self.assertRaisesRegex(RuntimeError, '校验失败'):
            download_models.verify_remote(self.directory, metadata, ['model.bin'])

    def test_remote_size_mismatch_and_lfs_pointer_are_rejected(self):
        (self.directory / 'model.bin').write_bytes(b'partial')
        with self.assertRaisesRegex(RuntimeError, '大小不符'):
            download_models.verify_remote(self.directory, [{'path': 'model.bin', 'size': 100}], ['model.bin'])
        (self.directory / 'model.bin').write_bytes(b'version https://git-lfs.github.com/spec/v1\n')
        self.assertEqual(models.missing_files(self.directory, {'model.bin': 1}), ['model.bin'])

    def test_flat_local_asr_does_not_need_hub_cache_index(self):
        folder = self.directory / 'models/asr/small'
        folder.mkdir(parents=True)
        required = {'model.bin': 1, 'config.json': 1, 'tokenizer.json': 1, 'vocabulary.txt': 1}
        for name in required:
            (folder / name).write_bytes(b'local model test')
        with patch.object(models, 'ASR_FILES', required):
            self.assertEqual(models.find_local_asr(self.directory), folder)
            (folder / 'tokenizer.json').unlink()
            self.assertIsNone(models.find_local_asr(self.directory))

    def test_missing_bigvgan_blocks_ready_status(self):
        with patch.object(service, 'index_missing_files', return_value=['hf_cache/bigvgan/bigvgan_generator.pt']), \
             patch.object(service, 'find_local_asr', return_value=self.directory):
            state = service.readiness()['index']
        self.assertFalse(state['ready'])
        self.assertEqual(state['repair'], '补下载BigVGAN.cmd')
        self.assertIn('BigVGAN', state['status'])

    def test_preflight_names_the_specific_repair_before_loading_models(self):
        with patch.object(models, 'index_missing_files', return_value=['hf_cache/bigvgan/bigvgan_generator.pt']):
            with self.assertRaisesRegex(RuntimeError, '补下载BigVGAN.cmd'):
                models.require_index_files(self.directory)


if __name__ == '__main__':
    unittest.main()
