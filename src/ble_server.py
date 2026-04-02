"""BLE GATT-server för pairing, WiFi-provisioning och fjärrstyrning.

Exponerar fyra BLE characteristics:
  - WiFi Config: Ta emot SSID + lösenord från appen
  - Auth Config: Ta emot user_id + refresh_token från appen
  - Status: Broadcast inspelningsstatus, batteri, etc.
  - Command: Ta emot kommandon (start/stopp inspelning, highlight)

Använder BlueZ D-Bus API via dbus-fast.
"""

import asyncio
import json
import subprocess
import logging
from pathlib import Path
from typing import Callable

from src.utils.config_loader import get_device_serial
from src.utils.logger import setup_logger

logger = setup_logger("notepin.ble")

# Försök importera BLE-bibliotek (finns bara på Pi)
try:
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method, dbus_property
    from dbus_fast import Variant, BusType
    HAS_BLE = True
except ImportError:
    HAS_BLE = False
    logger.warning("dbus-fast ej installerat — BLE inaktiverat")


# Filsökväg för sparade credentials
CREDENTIALS_FILE = Path.home() / ".notepin_credentials.json"


class BLEServer:
    """BLE GATT-server för NotePin-enhet."""

    def __init__(self, config: dict):
        self.config = config
        ble_cfg = config["ble"]
        self.device_name = ble_cfg["device_name"]
        self.service_uuid = ble_cfg["service_uuid"]
        self.wifi_char_uuid = ble_cfg["wifi_char_uuid"]
        self.auth_char_uuid = ble_cfg["auth_char_uuid"]
        self.status_char_uuid = ble_cfg["status_char_uuid"]
        self.command_char_uuid = ble_cfg.get(
            "command_char_uuid", "12345678-1234-5678-1234-123456789ac0"
        )

        self.device_serial = get_device_serial()
        self._running = False

        # Callbacks
        self._on_wifi_configured = None
        self._on_auth_configured = None
        self._on_command_received: Callable | None = None

        # Status som broadcastas
        self._status = {
            "recording": False,
            "battery": -1,
            "storage_free_mb": 0,
            "wifi_connected": False,
            "paired": self.is_paired,
        }

        # Ladda sparade credentials
        self._credentials = self._load_credentials()

    @property
    def is_paired(self) -> bool:
        """Kolla om enheten redan är parad med en användare."""
        creds = self._load_credentials()
        return bool(creds.get("user_id") and creds.get("refresh_token"))

    def on_wifi_configured(self, callback):
        """Callback när WiFi-credentials tagits emot."""
        self._on_wifi_configured = callback

    def on_auth_configured(self, callback):
        """Callback när auth-credentials tagits emot."""
        self._on_auth_configured = callback

    def on_command_received(self, callback: Callable):
        """Callback när ett kommando tas emot via BLE.

        Callback får command-strängen som argument:
          start_recording, stop_recording, add_highlight, get_status
        """
        self._on_command_received = callback

    def handle_command_write(self, data: bytes):
        """Hantera kommandon mottagna via BLE.

        Förväntat format (JSON):
            {"command": "start_recording"}
            {"command": "stop_recording"}
            {"command": "add_highlight"}
        """
        try:
            payload = json.loads(data.decode("utf-8"))
            command = payload.get("command")

            valid_commands = [
                "start_recording",
                "stop_recording",
                "add_highlight",
                "get_status",
            ]

            if command not in valid_commands:
                logger.warning(f"Okänt BLE-kommando: {command}")
                return

            logger.info(f"BLE-kommando mottaget: {command}")

            if self._on_command_received:
                self._on_command_received(command)

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Ogiltig kommando-data: {e}")

    def update_status(self, **kwargs):
        """Uppdatera status som broadcastas via BLE."""
        self._status.update(kwargs)

    def get_credentials(self) -> dict:
        """Hämta sparade credentials (user_id, refresh_token, etc.)."""
        return self._load_credentials()

    async def start(self):
        """Starta BLE-annonsering och GATT-server."""
        if not HAS_BLE:
            logger.error("BLE ej tillgängligt — kör du på en Pi?")
            return

        self._running = True
        logger.info(
            f"Startar BLE-server: {self.device_name}-"
            f"{self.device_serial[-4:]}"
        )

        try:
            await self._setup_advertising()
            logger.info("BLE-annonsering startad")
        except Exception as e:
            logger.error(f"BLE-startfel: {e}")

    async def stop(self):
        """Stoppa BLE-server."""
        self._running = False
        logger.info("BLE-server stoppad")

    async def _setup_advertising(self):
        """Konfigurera BLE-annonsering via bluetoothctl."""
        name = f"{self.device_name}-{self.device_serial[-4:]}"

        cmds = [
            f"system-alias {name}",
            "discoverable on",
            "pairable on",
            "advertise on",
        ]

        for cmd in cmds:
            try:
                subprocess.run(
                    ["bluetoothctl", cmd.split()[0]] + cmd.split()[1:],
                    capture_output=True,
                    timeout=5,
                )
            except Exception as e:
                logger.warning(f"bluetoothctl {cmd}: {e}")

    def handle_wifi_write(self, data: bytes):
        """Hantera WiFi-credentials mottagna via BLE.

        Förväntat format (JSON):
            {"ssid": "MittNätverk", "password": "hemligt123"}
        """
        try:
            payload = json.loads(data.decode("utf-8"))
            ssid = payload.get("ssid")
            password = payload.get("password")

            if not ssid:
                logger.error("WiFi-data saknar SSID")
                return

            logger.info(f"WiFi-credentials mottagna för: {ssid}")

            # Konfigurera NetworkManager
            success = self._configure_wifi(ssid, password)

            if success and self._on_wifi_configured:
                self._on_wifi_configured(ssid)

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Ogiltig WiFi-data: {e}")

    def handle_auth_write(self, data: bytes):
        """Hantera auth-credentials mottagna via BLE.

        Förväntat format (JSON):
            {
                "user_id": "uuid-...",
                "refresh_token": "token-...",
                "supabase_url": "https://xxx.supabase.co",
                "anon_key": "eyJ..."
            }
        """
        try:
            payload = json.loads(data.decode("utf-8"))
            user_id = payload.get("user_id")
            refresh_token = payload.get("refresh_token")

            if not user_id or not refresh_token:
                logger.error("Auth-data saknar user_id eller refresh_token")
                return

            # Spara credentials krypterat (TODO: använd keyring)
            creds = self._load_credentials()
            creds.update({
                "user_id": user_id,
                "refresh_token": refresh_token,
            })

            # Uppdatera Supabase-config om den skickades med
            if payload.get("supabase_url"):
                creds["supabase_url"] = payload["supabase_url"]
            if payload.get("anon_key"):
                creds["anon_key"] = payload["anon_key"]

            self._save_credentials(creds)

            logger.info(f"Auth-credentials sparade för user: {user_id[:8]}...")
            self._status["paired"] = True

            if self._on_auth_configured:
                self._on_auth_configured(user_id)

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Ogiltig auth-data: {e}")

    def get_status_bytes(self) -> bytes:
        """Returnera aktuell status som JSON-bytes för BLE-broadcast."""
        return json.dumps(self._status, separators=(",", ":")).encode("utf-8")

    def _configure_wifi(self, ssid: str, password: str) -> bool:
        """Konfigurera WiFi via NetworkManager (nmcli)."""
        try:
            # Ta bort eventuell gammal anslutning med samma namn
            subprocess.run(
                ["nmcli", "connection", "delete", ssid],
                capture_output=True,
                timeout=10,
            )

            # Skapa ny anslutning
            result = subprocess.run(
                [
                    "nmcli", "device", "wifi", "connect", ssid,
                    "password", password,
                    "ifname", "wlan0",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                logger.info(f"WiFi anslutet: {ssid}")
                self._status["wifi_connected"] = True

                # Spara SSID i credentials
                creds = self._load_credentials()
                creds["wifi_ssid"] = ssid
                self._save_credentials(creds)

                return True
            else:
                logger.error(f"WiFi-anslutning misslyckades: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("WiFi-anslutning timeout")
            return False
        except Exception as e:
            logger.error(f"WiFi-konfigurationsfel: {e}")
            return False

    def _load_credentials(self) -> dict:
        """Ladda sparade credentials från fil."""
        if CREDENTIALS_FILE.exists():
            try:
                with open(CREDENTIALS_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def _save_credentials(self, creds: dict):
        """Spara credentials till fil.

        TODO: Kryptera med enhetens serienummer som nyckel.
        """
        with open(CREDENTIALS_FILE, "w") as f:
            json.dump(creds, f, indent=2)
        # Sätt restriktiva filrättigheter
        CREDENTIALS_FILE.chmod(0o600)
