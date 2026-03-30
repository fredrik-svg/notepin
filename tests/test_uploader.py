"""Tester för uploader-modulen."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from src.uploader import Uploader


class TestUploader(unittest.TestCase):
    """Testa upload-kö och metadata."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = {
            "supabase": {
                "url": "https://test.supabase.co",
                "anon_key": "test-key",
                "storage_bucket": "recordings",
            },
            "paths": {
                "recordings_dir": self.tmpdir,
            },
            "device": {
                "upload_retry_interval": 5,
            },
        }
        self.credentials = {
            "user_id": "test-user-123",
            "refresh_token": "test-refresh-token",
        }

    def test_queue_creates_pending_file(self):
        """queue_upload ska skapa en JSON-fil i .pending."""
        uploader = Uploader(self.config, self.credentials)

        uploader.queue_upload(
            recording_id="rec-123",
            file_path="/tmp/test.wav",
            metadata={"duration_seconds": 60},
        )

        pending_dir = Path(self.tmpdir) / ".pending"
        pending_files = list(pending_dir.glob("*.json"))
        self.assertEqual(len(pending_files), 1)

        with open(pending_files[0]) as f:
            data = json.load(f)

        self.assertEqual(data["recording_id"], "rec-123")
        self.assertEqual(data["attempts"], 0)

    def test_queue_multiple_recordings(self):
        """Flera inspelningar ska kunna köas."""
        uploader = Uploader(self.config, self.credentials)

        for i in range(3):
            uploader.queue_upload(
                recording_id=f"rec-{i}",
                file_path=f"/tmp/test{i}.wav",
                metadata={},
            )

        pending_dir = Path(self.tmpdir) / ".pending"
        self.assertEqual(len(list(pending_dir.glob("*.json"))), 3)

    def test_generate_title(self):
        """Titeln ska baseras på tidpunkten."""
        uploader = Uploader(self.config, self.credentials)

        title = uploader._generate_title({
            "recorded_at": "2026-03-30T14:30:00+00:00"
        })

        self.assertIn("30", title)
        self.assertIn("14:30", title)

    def test_generate_title_fallback(self):
        """Vid saknad tidpunkt ska fallback-titel användas."""
        uploader = Uploader(self.config, self.credentials)
        title = uploader._generate_title({})
        self.assertEqual(title, "Inspelning")

    def test_no_user_id_warning(self):
        """Utan user_id ska uploader logga varning."""
        uploader = Uploader(self.config, {"refresh_token": "x"})
        self.assertIsNone(uploader.user_id)


if __name__ == "__main__":
    unittest.main()
