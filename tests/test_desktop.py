import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from voiceclone import desktop


class DesktopTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parent.parent / '.cache/test-temp'
        base.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'config.json').write_text(json.dumps({'port': 7865}), encoding='utf-8')

    def test_running_server_opens_browser_without_starting_another(self):
        with patch.object(desktop, 'backend_running', return_value=True), \
             patch.object(desktop.subprocess, 'Popen') as spawn, \
             patch.object(desktop.webbrowser, 'open') as browser:
            desktop.open_studio(self.root)
        spawn.assert_not_called()
        browser.assert_called_once_with('http://127.0.0.1:7865')

    def test_new_server_is_hidden_and_browser_waits_until_it_is_ready(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(desktop, 'backend_running', side_effect=[False, False, True]), \
             patch.object(desktop.subprocess, 'Popen', return_value=process) as spawn, \
             patch.object(desktop.webbrowser, 'open') as browser, \
             patch.object(desktop.time, 'sleep'):
            desktop.open_studio(self.root)
        args = spawn.call_args
        self.assertIn('--no-browser', args.args[0])
        self.assertEqual(args.kwargs['cwd'], self.root)
        self.assertEqual(args.kwargs['creationflags'], getattr(desktop.subprocess, 'CREATE_NO_WINDOW', 0))
        browser.assert_called_once_with('http://127.0.0.1:7865')

    def test_failed_start_reports_error_instead_of_opening_a_broken_page(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(desktop, 'backend_running', return_value=False), \
             patch.object(desktop.subprocess, 'Popen', return_value=process), \
             patch.object(desktop.webbrowser, 'open') as browser:
            with self.assertRaisesRegex(RuntimeError, 'studio.log'):
                desktop.open_studio(self.root)
        browser.assert_not_called()

    def test_unrelated_local_service_is_not_accepted(self):
        for payload in [{}, [], {'api_version': 3, 'engines': None}]:
            response = io.BytesIO(json.dumps(payload).encode())
            with patch.object(desktop.urllib.request, 'urlopen', return_value=response):
                self.assertFalse(desktop.backend_running('http://127.0.0.1:7865'))
