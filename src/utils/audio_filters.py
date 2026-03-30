"""Ljudfilter för att förbättra inspelningskvaliteten.

Körs i realtid på audio-chunks innan de skrivs till fil.
"""

import numpy as np


class AudioFilters:
    """Kedja av ljudfilter: högpass → noise gate → AGC."""

    def __init__(
        self,
        sample_rate: int = 44100,
        highpass_hz: int = 80,
        agc_enabled: bool = True,
        noise_gate_db: float = -45,
    ):
        self.sample_rate = sample_rate
        self.highpass_hz = highpass_hz
        self.agc_enabled = agc_enabled
        self.noise_gate_threshold = 10 ** (noise_gate_db / 20)

        # Högpassfilter-koefficient (enkel 1-pol IIR)
        rc = 1.0 / (2.0 * np.pi * highpass_hz)
        dt = 1.0 / sample_rate
        self.hp_alpha = rc / (rc + dt)
        self.hp_prev_input = 0.0
        self.hp_prev_output = 0.0

        # AGC-state
        self.agc_gain = 1.0
        self.agc_target_rms = 0.15  # Målnivå (0-1 skala)
        self.agc_attack = 0.01  # Snabb uppgång
        self.agc_release = 0.001  # Långsam nedgång

    def process(self, audio_data: np.ndarray) -> np.ndarray:
        """Processera en audio-chunk genom filterkedjan.

        Args:
            audio_data: numpy array med float32-samples (-1.0 till 1.0)

        Returns:
            Filtrerad audio som float32 numpy array
        """
        data = audio_data.astype(np.float32)

        if self.highpass_hz > 0:
            data = self._highpass(data)

        data = self._noise_gate(data)

        if self.agc_enabled:
            data = self._agc(data)

        return np.clip(data, -1.0, 1.0)

    def _highpass(self, data: np.ndarray) -> np.ndarray:
        """Första ordningens högpassfilter — tar bort lågfrekvent brum."""
        output = np.empty_like(data)

        prev_in = self.hp_prev_input
        prev_out = self.hp_prev_output
        alpha = self.hp_alpha

        for i in range(len(data)):
            output[i] = alpha * (prev_out + data[i] - prev_in)
            prev_in = data[i]
            prev_out = output[i]

        self.hp_prev_input = prev_in
        self.hp_prev_output = prev_out

        return output

    def _noise_gate(self, data: np.ndarray) -> np.ndarray:
        """Tystar ljud under tröskelvärdet — sparar utrymme och Whisper-tokens."""
        rms = np.sqrt(np.mean(data ** 2))
        if rms < self.noise_gate_threshold:
            return np.zeros_like(data)
        return data

    def _agc(self, data: np.ndarray) -> np.ndarray:
        """Automatic Gain Control — normaliserar volymen dynamiskt."""
        rms = np.sqrt(np.mean(data ** 2))

        if rms < 1e-6:
            return data

        desired_gain = self.agc_target_rms / rms

        # Begränsa gain för att undvika extrem förstärkning
        desired_gain = np.clip(desired_gain, 0.1, 10.0)

        # Mjuk övergång
        if desired_gain > self.agc_gain:
            rate = self.agc_attack
        else:
            rate = self.agc_release

        self.agc_gain += rate * (desired_gain - self.agc_gain)

        return data * self.agc_gain

    def reset(self):
        """Nollställ filterstate (vid ny inspelning)."""
        self.hp_prev_input = 0.0
        self.hp_prev_output = 0.0
        self.agc_gain = 1.0
