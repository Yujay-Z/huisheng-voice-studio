from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

from voiceclone import service


class LocalAsrTests(unittest.TestCase):
    def test_offline_recognition_uses_explicit_local_directory(self):
        local = Path(__file__).resolve().parent / 'example-local-asr'
        model = Mock()
        model.transcribe.return_value = ([types.SimpleNamespace(text='你好')], None)
        constructor = Mock(return_value=model)
        with patch.object(service, 'ASR', None), \
             patch.object(service, 'find_local_asr', return_value=local), \
             patch.dict('sys.modules', {'faster_whisper': types.SimpleNamespace(WhisperModel=constructor)}):
            self.assertEqual(service.transcribe('audio.wav', 'zh', offline=True), '你好')
        self.assertEqual(constructor.call_args.args[0], str(local))
        self.assertIs(constructor.call_args.kwargs['local_files_only'], True)
        self.assertEqual(model.transcribe.call_args.kwargs['initial_prompt'], '以下是普通话的简体中文转写。')

    def test_missing_local_files_fail_without_repo_lookup(self):
        constructor = Mock()
        with patch.object(service, 'ASR', None), \
             patch.object(service, 'find_local_asr', return_value=None), \
             patch.dict('sys.modules', {'faster_whisper': types.SimpleNamespace(WhisperModel=constructor)}):
            with self.assertRaisesRegex(RuntimeError, '本地识别模型未安装完整'):
                service.transcribe('audio.wav', 'zh', offline=True)
        constructor.assert_not_called()
