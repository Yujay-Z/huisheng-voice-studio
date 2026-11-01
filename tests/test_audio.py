import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from voiceclone import audio, separation


class AudioTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parent.parent / '.cache/test-temp'
        base.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.source = self.directory / ('a' * 32 + '.wav')
        self.rate = 24000
        wave = np.sin(np.arange(self.rate * 3) * .07).astype('float32') * .2
        sf.write(self.source, np.column_stack([wave, wave * .5]), self.rate, subtype='PCM_16')

    def test_waveform_is_real_bounded_and_keeps_stereo_metadata(self):
        info = audio.describe(self.source)
        self.assertEqual(info['seconds'], 3)
        self.assertEqual(info['channels'], 2)
        self.assertLessEqual(len(info['peaks']), 640)
        self.assertTrue(all(0 <= value <= 1 for value in info['peaks']))
        self.assertAlmostEqual(max(info['peaks']), 1)

    def test_crop_matches_samples_and_preserves_original(self):
        before = self.source.read_bytes()
        info = audio.crop_upload(self.source, self.directory, .5, 1.75)
        output = self.directory / (info['id'] + '.wav')
        actual, rate = sf.read(output, dtype='int16')
        expected, _ = sf.read(self.source, dtype='int16')
        np.testing.assert_array_equal(actual, expected[12000:42000])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(rate, self.rate)
        self.assertEqual(info['seconds'], 1.25)

    def test_invalid_selection_is_rejected_without_publishing_a_file(self):
        for start, end in [(0, .99), (-1, 2), (0, 4), (2, 1), (float('nan'), 2), (0, float('inf'))]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                audio.crop_upload(self.source, self.directory, start, end)
        self.assertEqual(list(self.directory.glob('*.wav')), [self.source])

    def test_separation_checks_mode_before_spawning_work(self):
        with self.assertRaises(ValueError):
            separation.start(self.directory, self.directory, self.source, {'mode': 'invalid', 'start': 0, 'end': 2})

    def test_processing_status_reads_atomic_result_and_freezes_elapsed(self):
        task = {'id': 'test', 'state': 'queued', 'created': 100, '_directory': self.directory}
        (self.directory / 'status.json').write_text(json.dumps({'state': 'ready', 'finished_at': 110, 'vocals': {'id': 'voice'}}), encoding='utf-8')
        with patch.object(separation.time, 'time', return_value=999):
            result = separation.status(task)
        self.assertEqual(result['elapsed_seconds'], 10)
        self.assertEqual(result['vocals']['id'], 'voice')
        self.assertNotIn('_directory', result)
