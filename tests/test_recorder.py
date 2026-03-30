"""Tester för recorder-modulen."""

import os
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.utils.audio_filters import AudioFilters

import numpy as np


class TestAudioFilters(unittest.TestCase):
    """Testa ljudfilter."""

    def setUp(self):
        self.filters = AudioFilters(
            sample_rate=44100,
            highpass_hz=80,
            agc_enabled=True,
            noise_gate_db=-45,
        )

    def test_process_returns_same_shape(self):
        """Output ska ha samma form som input."""
        data = np.random.randn(4096).astype(np.float32) * 0.5
        result = self.filters.process(data)
        self.assertEqual(result.shape, data.shape)

    def test_process_clips_output(self):
        """Output ska vara klippt till -1.0 — 1.0."""
        data = np.ones(1000, dtype=np.float32) * 5.0
        result = self.filters.process(data)
        self.assertTrue(np.all(result <= 1.0))
        self.assertTrue(np.all(result >= -1.0))

    def test_noise_gate_silences_quiet_input(self):
        """Noise gate ska tysta ljud under tröskeln."""
        # Mycket tyst signal
        data = np.ones(1000, dtype=np.float32) * 0.00001
        result = self.filters.process(data)
        self.assertTrue(np.all(result == 0))

    def test_noise_gate_passes_loud_input(self):
        """Noise gate ska släppa igenom ljud över tröskeln."""
        data = np.random.randn(1000).astype(np.float32) * 0.5
        result = self.filters.process(data)
        self.assertFalse(np.all(result == 0))

    def test_highpass_removes_dc_offset(self):
        """Högpassfiltret ska ta bort DC-offset."""
        # Konstant signal (DC) bör filtreras bort
        data = np.ones(44100, dtype=np.float32) * 0.5
        # Kör flera chunks för att låta filtret stabilisera sig
        for _ in range(10):
            result = self.filters.process(data)
        # Sista chunken bör ha nära noll-output
        self.assertAlmostEqual(np.mean(np.abs(result)), 0, places=1)

    def test_reset_clears_state(self):
        """Reset ska nollställa filterstate."""
        data = np.random.randn(1000).astype(np.float32) * 0.5
        self.filters.process(data)

        self.filters.reset()

        self.assertEqual(self.filters.hp_prev_input, 0.0)
        self.assertEqual(self.filters.hp_prev_output, 0.0)
        self.assertEqual(self.filters.agc_gain, 1.0)


class TestRecorderConfig(unittest.TestCase):
    """Testa recorder-konfiguration (utan riktig hårdvara)."""

    def _make_config(self, **overrides):
        config = {
            "audio": {
                "sample_rate": 44100,
                "bit_depth": 24,
                "channels": 2,
                "format": "wav",
                "highpass_hz": 80,
                "agc_enabled": True,
                "noise_gate_db": -45,
                "max_duration_hours": 4,
            },
            "paths": {
                "recordings_dir": tempfile.mkdtemp(),
            },
        }
        config["audio"].update(overrides)
        return config

    def test_creates_recordings_dir(self):
        """Recordings-mappen ska skapas automatiskt."""
        config = self._make_config()
        from src.recorder import Recorder
        recorder = Recorder(config)
        self.assertTrue(
            Path(config["paths"]["recordings_dir"]).exists()
        )

    def test_default_not_recording(self):
        """Recorder ska inte spela in vid start."""
        config = self._make_config()
        from src.recorder import Recorder
        recorder = Recorder(config)
        self.assertFalse(recorder.is_recording)
        self.assertIsNone(recorder.current_recording_id)


if __name__ == "__main__":
    unittest.main()
