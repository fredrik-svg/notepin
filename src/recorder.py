"""Ljudinspelning via WM8960 Audio HAT.

Spelar in ljud i chunks, kör genom audiofilter, och skriver till
WAV eller FLAC på SD-kortet. Hanterar start/stopp/highlight via callbacks.
"""

import io
import os
import uuid
import wave
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from src.utils.audio_filters import AudioFilters
from src.utils.logger import setup_logger

logger = setup_logger("notepin.recorder")

# Chunk-storlek i frames (antal samples per callback)
CHUNK_FRAMES = 4096


class Recorder:
    """Hanterar ljudinspelning från WM8960 HAT."""

    def __init__(self, config: dict):
        audio_cfg = config["audio"]
        self.sample_rate = audio_cfg.get("sample_rate", 44100)
        self.bit_depth = audio_cfg.get("bit_depth", 24)
        self.channels = audio_cfg.get("channels", 2)
        self.output_format = audio_cfg.get("format", "flac")
        self.max_duration = audio_cfg.get("max_duration_hours", 4) * 3600
        self.recordings_dir = Path(config["paths"]["recordings_dir"])
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

        self.filters = AudioFilters(
            sample_rate=self.sample_rate,
            highpass_hz=audio_cfg.get("highpass_hz", 80),
            agc_enabled=audio_cfg.get("agc_enabled", True),
            noise_gate_db=audio_cfg.get("noise_gate_db", -45),
        )

        # State
        self._recording = False
        self._thread: threading.Thread | None = None
        self._current_recording_id: str | None = None
        self._current_file_path: str | None = None
        self._start_time: float | None = None
        self._highlights: list[dict] = []

        # Callbacks
        self._on_recording_started: Callable | None = None
        self._on_recording_stopped: Callable | None = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def current_recording_id(self) -> str | None:
        return self._current_recording_id

    @property
    def duration_seconds(self) -> int:
        if self._start_time and self._recording:
            return int(time.time() - self._start_time)
        return 0

    def on_recording_started(self, callback: Callable):
        """Registrera callback som anropas när inspelning startar."""
        self._on_recording_started = callback

    def on_recording_stopped(self, callback: Callable):
        """Registrera callback som anropas när inspelning stoppas.

        Callback får (recording_id, file_path, metadata) som argument.
        """
        self._on_recording_stopped = callback

    def start(self) -> str:
        """Starta en ny inspelning.

        Returns:
            recording_id (UUID) för den nya inspelningen
        """
        if self._recording:
            logger.warning("Inspelning pågår redan")
            return self._current_recording_id

        self._current_recording_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{self._current_recording_id[:8]}"

        # Spela alltid in som WAV, konvertera till FLAC efteråt
        self._current_file_path = str(
            self.recordings_dir / f"{filename}.wav"
        )

        self._highlights = []
        self._recording = True
        self._start_time = time.time()
        self.filters.reset()

        self._thread = threading.Thread(
            target=self._record_loop,
            daemon=True,
            name="recorder",
        )
        self._thread.start()

        logger.info(
            f"Inspelning startad: {self._current_recording_id[:8]} "
            f"({self.sample_rate}Hz, {self.bit_depth}bit, "
            f"{self.channels}ch)"
        )

        if self._on_recording_started:
            self._on_recording_started()

        return self._current_recording_id

    def stop(self) -> dict | None:
        """Stoppa pågående inspelning.

        Returns:
            Metadata-dict med recording_id, file_path, duration etc.
            None om ingen inspelning pågår.
        """
        if not self._recording:
            logger.warning("Ingen inspelning att stoppa")
            return None

        self._recording = False

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        duration = int(time.time() - self._start_time)
        file_path = self._current_file_path

        # Konvertera till FLAC om konfigurerat
        if self.output_format == "flac" and file_path.endswith(".wav"):
            flac_path = file_path.replace(".wav", ".flac")
            try:
                file_path = self._convert_to_flac(file_path, flac_path)
            except Exception as e:
                logger.error(f"FLAC-konvertering misslyckades: {e}")
                # Behåll WAV-filen

        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

        metadata = {
            "recording_id": self._current_recording_id,
            "file_path": file_path,
            "duration_seconds": duration,
            "file_size_bytes": file_size,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "highlights": self._highlights,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            f"Inspelning stoppad: {self._current_recording_id[:8]} "
            f"({duration}s, {file_size / 1024 / 1024:.1f} MB)"
        )

        if self._on_recording_stopped:
            self._on_recording_stopped(
                self._current_recording_id,
                file_path,
                metadata,
            )

        self._current_recording_id = None
        self._current_file_path = None
        self._start_time = None

        return metadata

    def add_highlight(self, label: str = ""):
        """Markera en viktig punkt i inspelningen (kort knapptryck)."""
        if not self._recording or not self._start_time:
            return

        timestamp_ms = int((time.time() - self._start_time) * 1000)
        highlight = {
            "timestamp_ms": timestamp_ms,
            "label": label or f"Markering {len(self._highlights) + 1}",
        }
        self._highlights.append(highlight)
        logger.info(
            f"Highlight vid {timestamp_ms}ms: {highlight['label']}"
        )

    def _record_loop(self):
        """Inspelningsloop som körs i egen tråd."""
        import pyaudio

        pa = pyaudio.PyAudio()
        wav_file = None

        try:
            # Hitta WM8960-enheten
            device_index = self._find_audio_device(pa)

            stream = pa.open(
                format=pyaudio.paInt32 if self.bit_depth == 24 else pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=CHUNK_FRAMES,
            )

            # Öppna WAV-fil för skrivning
            wav_file = wave.open(self._current_file_path, "wb")
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(4 if self.bit_depth == 24 else 2)
            wav_file.setframerate(self.sample_rate)

            logger.info("Inspelningsström öppnad")

            while self._recording:
                # Kolla max duration
                if time.time() - self._start_time > self.max_duration:
                    logger.warning("Max inspelningslängd nådd, stoppar")
                    self._recording = False
                    break

                try:
                    raw_data = stream.read(
                        CHUNK_FRAMES, exception_on_overflow=False
                    )
                except IOError as e:
                    logger.warning(f"Audio overflow: {e}")
                    continue

                # Konvertera till numpy för filtrering
                if self.bit_depth == 24:
                    audio_np = np.frombuffer(raw_data, dtype=np.int32)
                    audio_float = audio_np.astype(np.float32) / 2147483648.0
                else:
                    audio_np = np.frombuffer(raw_data, dtype=np.int16)
                    audio_float = audio_np.astype(np.float32) / 32768.0

                # Kör filter (mono-mix för filtrering, behåll stereo i output)
                if self.channels == 2:
                    mono = (audio_float[0::2] + audio_float[1::2]) / 2
                    filtered_mono = self.filters.process(mono)
                    # Om noise gate stängde av, tysta båda kanaler
                    if np.all(filtered_mono == 0):
                        audio_float[:] = 0
                else:
                    audio_float = self.filters.process(audio_float)

                # Konvertera tillbaka och skriv
                if self.bit_depth == 24:
                    output = (audio_float * 2147483648.0).astype(np.int32)
                else:
                    output = (audio_float * 32768.0).astype(np.int16)

                wav_file.writeframes(output.tobytes())

            stream.stop_stream()
            stream.close()

        except Exception as e:
            logger.error(f"Inspelningsfel: {e}")

        finally:
            if wav_file:
                wav_file.close()
            pa.terminate()

    def _find_audio_device(self, pa) -> int | None:
        """Hitta WM8960 Audio HAT:ens device index."""
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = info.get("name", "").lower()
            if "wm8960" in name or "seeed" in name:
                logger.info(f"Hittade WM8960 på device {i}: {info['name']}")
                return i

        # Fallback: använd standardinput
        logger.warning(
            "WM8960 hittades inte, använder standardinput. "
            "Kontrollera att Audio HAT är korrekt monterad."
        )
        return None

    def _convert_to_flac(self, wav_path: str, flac_path: str) -> str:
        """Konvertera WAV till FLAC för att spara utrymme (~50%)."""
        data, samplerate = sf.read(wav_path)
        sf.write(flac_path, data, samplerate, format="FLAC")

        # Ta bort WAV-filen
        os.remove(wav_path)
        logger.info(
            f"Konverterade till FLAC: "
            f"{os.path.getsize(flac_path) / 1024 / 1024:.1f} MB"
        )
        return flac_path
