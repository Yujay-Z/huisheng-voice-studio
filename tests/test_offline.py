import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from distribution.build_offline import Entry, digest_file, write_volumes
from voiceclone import runtime

ROOT = Path(__file__).resolve().parent.parent


class RuntimeTests(unittest.TestCase):
    def test_development_and_relocated_runtime_selection(self):
        base = ROOT / '.cache/test-temp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            self.assertEqual(runtime.engine_python(root), root / 'engines/index/.venv/Scripts/python.exe')
            self.assertEqual(runtime.app_python(root, True), root / '.venv/Scripts/pythonw.exe')
            for role in ['app', 'engine']:
                (root / 'runtime' / role).mkdir(parents=True)
                (root / 'runtime' / role / 'python.exe').touch()
                (root / 'runtime' / role / 'pythonw.exe').touch()
            self.assertEqual(runtime.engine_python(root), root / 'runtime/engine/python.exe')
            self.assertEqual(runtime.app_python(root, True), root / 'runtime/app/pythonw.exe')
            (root / 'offline-package.json').write_text('{}')
            with patch.dict(os.environ, {}, clear=True):
                runtime.configure_runtime(root)
                self.assertEqual(os.environ['HF_HUB_OFFLINE'], '1')
                self.assertEqual(os.environ['TRANSFORMERS_OFFLINE'], '1')
                self.assertEqual(os.environ['HF_HOME'], str(root / '.cache/huggingface'))


@unittest.skipUnless(os.name == 'nt', 'Windows offline installer')
class OfflineInstallerTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.cache/test-temp'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.target = self.root / '安装 测试'
        self.status = self.root / 'status.json'
        self.expected = b'weight-contents-' * 50
        entries = [Entry('readme.txt', data=b'hello offline'), Entry('weights/test.bin', data=self.expected)]
        volumes, chunks, files, total = write_volumes(entries, self.bundle, 'test', 240, 160)
        self.manifest = {'format': 1, 'package_id': 'test-package', 'version': 'test',
                         'installed_bytes': total, 'volumes': volumes, 'chunks': chunks}
        (self.bundle / 'offline-manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        self.script = self.bundle / 'Install-Offline.ps1'
        self.script.write_text((ROOT / 'distribution/Install-Offline.ps1').read_text(encoding='utf-8-sig'), encoding='utf-8-sig')

    def install(self):
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(self.script), '-Worker', '-Bundle', str(self.bundle),
                                 '-Target', str(self.target), '-StatusFile', str(self.status),
                                 '-SkipRuntime', '-SkipShortcut', '-SkipCheck'],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                creationflags=subprocess.CREATE_NO_WINDOW)
        return result, json.loads(self.status.read_text(encoding='utf-8'))

    def test_restore_split_weights_and_reinstall_without_network_or_system_changes(self):
        result, status = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status['state'], 'done')
        self.assertEqual((self.target / 'weights/test.bin').read_bytes(), self.expected)
        self.assertEqual((self.target / 'readme.txt').read_bytes(), b'hello offline')
        again, status = self.install()
        self.assertEqual(again.returncode, 0, again.stderr)

    def test_missing_later_volume_can_resume_without_repeating_completed_volumes(self):
        volume = self.bundle / self.manifest['volumes'][1]['name']
        missing = volume.with_suffix('.missing')
        self.assertTrue(volume.resolve().is_relative_to(self.root.resolve()))
        volume.rename(missing)
        failed, status = self.install()
        self.assertNotEqual(failed.returncode, 0)
        record = json.loads((self.target / '.offline-install-progress.json').read_text())
        self.assertEqual(record['completed_volumes'], 1)
        missing.rename(volume)
        resumed, status = self.install()
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual((self.target / 'weights/test.bin').read_bytes(), self.expected)

    def test_changed_archive_is_rejected_before_extraction(self):
        volume = self.bundle / self.manifest['volumes'][0]['name']
        raw = bytearray(volume.read_bytes())
        raw[10] ^= 1
        volume.write_bytes(raw)
        result, status = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(status['state'], 'error')
        self.assertFalse((self.target / 'readme.txt').exists())

    def test_existing_unrelated_directory_is_not_overwritten(self):
        self.target.mkdir()
        personal = self.target / 'keep.txt'
        personal.write_text('keep my files')
        result, status = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(personal.read_text(), 'keep my files')
        self.assertFalse((self.target / 'readme.txt').exists())


if __name__ == '__main__':
    unittest.main()
