"""NotePin Pi — Huvudprocess.

Startar och koordinerar alla subsystem:
  1. OTA-uppdatering (vid boot)
  2. LED-kontroller
  3. BLE GATT-server (pairing + status)
  4. Knapphantering (inspelning start/stopp/highlight)
  5. Ljudinspelning
  6. Supabase-upload (bakgrund)

Körs som systemd-service via notepin.service.
"""

import asyncio
import signal
import sys
from pathlib import Path

from src.utils.config_loader import load_config, get_device_serial
from src.utils.logger import setup_logger
from src.recorder import Recorder
from src.ble_server import BLEServer
from src.uploader import Uploader
from src.button_handler import ButtonHandler, SimulatedButton, HAS_GPIO
from src.led_controller import LEDController, LEDState
from src.updater import check_for_updates, restart_service

logger = setup_logger("notepin.main")


class NotePin:
    """Huvudklass som koordinerar alla NotePin-subsystem."""

    def __init__(self):
        logger.info("=" * 50)
        logger.info("NotePin Pi startar...")
        logger.info(f"Device serial: {get_device_serial()}")
        logger.info("=" * 50)

        # Ladda konfiguration
        self.config = load_config()

        # Initiera subsystem
        self.led = LEDController(self.config)
        self.ble = BLEServer(self.config)
        self.recorder = Recorder(self.config)

        # Knapp — riktig GPIO eller simulerad
        if HAS_GPIO:
            self.button = ButtonHandler(self.config)
        else:
            self.button = SimulatedButton()

        # Uploader skapas efter pairing (behöver credentials)
        self.uploader: Uploader | None = None

        self._setup_callbacks()
        self._running = False

    def _setup_callbacks(self):
        """Koppla ihop subsystemen via callbacks."""

        # Knapp → inspelning
        self.button.on_long_press(self._toggle_recording)
        self.button.on_short_press(self._on_highlight)

        # Inspelning → LED + upload-kö
        self.recorder.on_recording_started(self._on_recording_started)
        self.recorder.on_recording_stopped(self._on_recording_stopped)

        # BLE → WiFi + auth
        self.ble.on_wifi_configured(self._on_wifi_configured)
        self.ble.on_auth_configured(self._on_auth_configured)

    def _toggle_recording(self):
        """Starta eller stoppa inspelning (långt knapptryck)."""
        if self.recorder.is_recording:
            self.recorder.stop()
        else:
            self.recorder.start()

    def _on_highlight(self):
        """Lägg till highlight (kort knapptryck)."""
        if self.recorder.is_recording:
            self.recorder.add_highlight()
            self.led.flash_highlight()
        else:
            logger.debug("Kort tryck ignorerat — ingen inspelning pågår")

    def _on_recording_started(self):
        """Callback när inspelning startar."""
        self.led.set_state(LEDState.RECORDING)
        self.ble.update_status(recording=True)

    def _on_recording_stopped(
        self, recording_id: str, file_path: str, metadata: dict
    ):
        """Callback när inspelning stoppas — köa för upload."""
        self.led.set_state(LEDState.STANDBY)
        self.ble.update_status(recording=False)

        # Lägg till device_id i metadata
        credentials = self.ble.get_credentials()
        metadata["device_id"] = credentials.get("device_id")

        # Köa för upload
        if self.uploader:
            self.uploader.queue_upload(recording_id, file_path, metadata)
            logger.info("Inspelning köad för upload")
        else:
            logger.warning(
                "Uploader ej initierad — inspelning sparad lokalt. "
                "Para ihop enheten med appen för att aktivera upload."
            )

    def _on_wifi_configured(self, ssid: str):
        """Callback när WiFi konfigurerats via BLE."""
        logger.info(f"WiFi konfigurerat: {ssid}")
        self.ble.update_status(wifi_connected=True)

        # Försök initiera uploader om vi har auth
        self._try_init_uploader()

    def _on_auth_configured(self, user_id: str):
        """Callback när auth-credentials tagits emot via BLE."""
        logger.info(f"Parad med användare: {user_id[:8]}...")

        # Registrera enheten i Supabase
        self._try_init_uploader()

    def _try_init_uploader(self):
        """Försök skapa uploader om alla credentials finns."""
        credentials = self.ble.get_credentials()

        if not credentials.get("user_id") or not credentials.get("refresh_token"):
            return

        if self.uploader:
            return

        self.uploader = Uploader(self.config, credentials)
        logger.info("Uploader initierad — redo att synka inspelningar")

    async def run(self):
        """Huvudloop — starta alla subsystem."""
        self._running = True

        # 1. OTA-uppdatering
        if self.config["device"].get("check_updates_on_boot"):
            logger.info("Söker efter uppdateringar...")
            if check_for_updates():
                logger.info("Uppdatering tillämpad — startar om")
                restart_service()
                return

        # 2. Starta LED
        self.led.start()
        self.led.set_state(LEDState.STANDBY)

        # 3. Starta BLE
        await self.ble.start()

        if not self.ble.is_paired:
            self.led.set_state(LEDState.PAIRING)
            logger.info(
                "Enheten är inte parad — väntar på BLE-anslutning från appen"
            )
        else:
            # Initiera uploader med sparade credentials
            self._try_init_uploader()

        # 4. Starta knapphantering
        self.button.start()

        # 5. Starta upload-loop i bakgrunden
        upload_task = None
        if self.uploader:
            upload_task = asyncio.create_task(self.uploader.start())

        # 6. Status-broadcast loop
        logger.info("NotePin redo!")
        logger.info(
            "Långt tryck = starta/stoppa inspelning, "
            "kort tryck = highlight"
        )

        try:
            while self._running:
                # Uppdatera BLE-status
                self.ble.update_status(
                    recording=self.recorder.is_recording,
                    duration=self.recorder.duration_seconds,
                )

                # Kolla om uploader ska startas (kan hända efter BLE-pairing)
                if not upload_task and self.uploader:
                    upload_task = asyncio.create_task(self.uploader.start())

                await asyncio.sleep(
                    self.config["device"].get(
                        "status_broadcast_interval", 5
                    )
                )

        except asyncio.CancelledError:
            logger.info("Huvudloop avbruten")

        finally:
            await self.shutdown()

    async def shutdown(self):
        """Stäng ner alla subsystem snyggt."""
        logger.info("Stänger ner NotePin...")
        self._running = False

        # Stoppa inspelning om den pågår
        if self.recorder.is_recording:
            logger.info("Stoppar pågående inspelning...")
            self.recorder.stop()

        # Stoppa subsystem
        self.button.stop()

        if self.uploader:
            await self.uploader.stop()

        await self.ble.stop()

        self.led.set_state(LEDState.OFF)
        self.led.stop()

        logger.info("NotePin avstängd")


def main():
    """Entry point."""
    notepin = NotePin()

    # Hantera SIGTERM/SIGINT för clean shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler():
        logger.info("Signal mottagen — stänger ner...")
        notepin._running = False

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        loop.run_until_complete(notepin.run())
    except KeyboardInterrupt:
        loop.run_until_complete(notepin.shutdown())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
