"""Tester för BLE-serverns credential-hantering."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ble_server import BLEServer


class TestBLECredentials(unittest.TestCase):
    """Testa credential-hantering (utan riktig BLE-hårdvara)."""

    def setUp(self):
        self.config = {
            "ble": {
                "device_name": "TestPin",
                "service_uuid": "12345678-1234-5678-1234-123456789abc",
                "wifi_char_uuid": "12345678-1234-5678-1234-123456789abd",
                "auth_char_uuid": "12345678-1234-5678-1234-123456789abe",
                "status_char_uuid": "12345678-1234-5678-1234-123456789abf",
            },
        }
        # Använd temporär credentials-fil
        self.tmp_creds = Path(tempfile.mktemp(suffix=".json"))

    def tearDown(self):
        if self.tmp_creds.exists():
            self.tmp_creds.unlink()

    @patch("src.ble_server.CREDENTIALS_FILE")
    def test_handle_auth_write(self, mock_path):
        """Auth-credentials ska sparas korrekt."""
        mock_path.__str__ = lambda s: str(self.tmp_creds)
        mock_path.exists = lambda: self.tmp_creds.exists()
        mock_path.chmod = lambda m: None

        # Peka om till temp-fil
        with patch("src.ble_server.CREDENTIALS_FILE", self.tmp_creds):
            server = BLEServer(self.config)

            auth_data = json.dumps({
                "user_id": "user-abc-123",
                "refresh_token": "token-xyz-789",
            }).encode("utf-8")

            server.handle_auth_write(auth_data)

            # Verifiera att credentials sparades
            with open(self.tmp_creds) as f:
                creds = json.load(f)

            self.assertEqual(creds["user_id"], "user-abc-123")
            self.assertEqual(creds["refresh_token"], "token-xyz-789")

    @patch("src.ble_server.CREDENTIALS_FILE")
    def test_handle_wifi_invalid_json(self, mock_path):
        """Ogiltig JSON ska loggas som fel utan krasch."""
        mock_path.exists = lambda: False

        with patch("src.ble_server.CREDENTIALS_FILE", self.tmp_creds):
            server = BLEServer(self.config)
            # Ska inte krascha
            server.handle_wifi_write(b"not json{{{")

    def test_get_status_bytes(self):
        """Status ska returneras som valid JSON."""
        with patch("src.ble_server.CREDENTIALS_FILE", self.tmp_creds):
            server = BLEServer(self.config)
            server.update_status(battery=75, recording=True)

            status_bytes = server.get_status_bytes()
            status = json.loads(status_bytes.decode("utf-8"))

            self.assertEqual(status["battery"], 75)
            self.assertTrue(status["recording"])

    def test_status_default_values(self):
        """Default-status ska ha vettiga värden."""
        with patch("src.ble_server.CREDENTIALS_FILE", self.tmp_creds):
            server = BLEServer(self.config)
            status = json.loads(
                server.get_status_bytes().decode("utf-8")
            )

            self.assertFalse(status["recording"])
            self.assertIn("battery", status)
            self.assertIn("wifi_connected", status)


if __name__ == "__main__":
    unittest.main()
