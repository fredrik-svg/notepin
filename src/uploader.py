"""Upload av inspelningar till Supabase Storage + metadata till Postgres.

Kör som bakgrundsprocess som bevakar recordings-mappen och laddar upp
nya filer när WiFi är tillgängligt. Hanterar retry vid nätverksfel.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone

import httpx

from src.utils.logger import setup_logger

logger = setup_logger("notepin.uploader")

# Maximal filstorlek att ladda upp i en chunk (5 MB)
CHUNK_SIZE = 5 * 1024 * 1024


class Uploader:
    """Laddar upp inspelningar till Supabase."""

    def __init__(self, config: dict, credentials: dict):
        self.config = config
        self.recordings_dir = Path(config["paths"]["recordings_dir"])
        self.retry_interval = config["device"].get("upload_retry_interval", 60)

        # Supabase-config — kan komma från config.yaml ELLER BLE-credentials
        self.supabase_url = (
            credentials.get("supabase_url")
            or config["supabase"]["url"]
        )
        self.anon_key = (
            credentials.get("anon_key")
            or config["supabase"]["anon_key"]
        )
        self.bucket = config["supabase"]["storage_bucket"]

        # Auth
        self.user_id = credentials.get("user_id")
        self.refresh_token = credentials.get("refresh_token")
        self.access_token: str | None = None
        self.token_expires_at: float = 0

        # Upload-kö: metadata-filer som väntar
        self._running = False
        self._pending_dir = self.recordings_dir / ".pending"
        self._pending_dir.mkdir(parents=True, exist_ok=True)

    async def start(self):
        """Starta upload-loopen i bakgrunden."""
        if not self.user_id:
            logger.warning("Ingen user_id — uploader väntar på pairing")
            return

        self._running = True
        logger.info("Uploader startad")

        while self._running:
            try:
                if await self._has_wifi():
                    await self._refresh_token_if_needed()
                    await self._process_pending()
                else:
                    logger.debug("Inget WiFi — väntar...")
            except Exception as e:
                logger.error(f"Upload-loop fel: {e}")

            await asyncio.sleep(self.retry_interval)

    async def stop(self):
        """Stoppa upload-loopen."""
        self._running = False
        logger.info("Uploader stoppad")

    def queue_upload(self, recording_id: str, file_path: str, metadata: dict):
        """Lägg till en inspelning i upload-kön.

        Sparar metadata som JSON-fil i .pending-mappen.
        """
        pending_file = self._pending_dir / f"{recording_id}.json"
        pending_data = {
            "recording_id": recording_id,
            "file_path": file_path,
            "metadata": metadata,
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "attempts": 0,
        }

        with open(pending_file, "w") as f:
            json.dump(pending_data, f)

        logger.info(f"Inspelning köad för upload: {recording_id[:8]}")

    async def _process_pending(self):
        """Gå igenom alla pending-filer och ladda upp."""
        pending_files = sorted(self._pending_dir.glob("*.json"))

        if not pending_files:
            return

        logger.info(f"{len(pending_files)} inspelning(ar) att ladda upp")

        for pending_file in pending_files:
            if not self._running:
                break

            try:
                with open(pending_file, "r") as f:
                    data = json.load(f)

                success = await self._upload_recording(data)

                if success:
                    pending_file.unlink()
                    logger.info(
                        f"Upload klar: {data['recording_id'][:8]}"
                    )
                else:
                    # Öka attempt-counter
                    data["attempts"] = data.get("attempts", 0) + 1
                    with open(pending_file, "w") as f:
                        json.dump(data, f)

            except Exception as e:
                logger.error(f"Fel vid upload av {pending_file.name}: {e}")

    async def _upload_recording(self, data: dict) -> bool:
        """Ladda upp en inspelning till Supabase.

        1. Ladda upp ljudfil till Storage
        2. Skapa rad i recordings-tabellen
        """
        file_path = data["file_path"]
        recording_id = data["recording_id"]
        metadata = data["metadata"]

        if not os.path.exists(file_path):
            logger.error(f"Fil saknas: {file_path}")
            return True  # Ta bort från kön, filen finns inte

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                headers = self._auth_headers()

                # 1. Upload fil till Storage
                # Format: {user_id}/{timestamp}.flac (matchar Lovable-appens förväntning)
                file_ext = "flac" if file_path.endswith(".flac") else "wav"
                timestamp = metadata.get("recorded_at", "unknown").replace(":", "-")
                storage_path = f"{self.user_id}/{timestamp}.{file_ext}"

                content_type = (
                    "audio/flac" if file_path.endswith(".flac")
                    else "audio/wav"
                )

                with open(file_path, "rb") as f:
                    file_data = f.read()

                upload_url = (
                    f"{self.supabase_url}/storage/v1/object/"
                    f"{self.bucket}/{storage_path}"
                )

                resp = await client.post(
                    upload_url,
                    headers={
                        **headers,
                        "Content-Type": content_type,
                    },
                    content=file_data,
                )

                if resp.status_code not in (200, 201):
                    logger.error(
                        f"Storage upload misslyckades ({resp.status_code}): "
                        f"{resp.text}"
                    )
                    return False

                logger.info(f"Fil uppladdad: {storage_path}")

                # 2. Skapa recordings-rad
                recording_data = {
                    "id": recording_id,
                    "user_id": self.user_id,
                    "device_id": metadata.get("device_id"),
                    "title": self._generate_title(metadata),
                    "duration_seconds": metadata.get("duration_seconds", 0),
                    "file_path": storage_path,
                    "file_size_bytes": metadata.get("file_size_bytes", 0),
                    "status": "uploaded",
                    "language": "sv",
                    "highlights": metadata.get("highlights", []),
                    "recorded_at": metadata.get("recorded_at"),
                }

                db_url = f"{self.supabase_url}/rest/v1/recordings"
                resp = await client.post(
                    db_url,
                    headers={
                        **headers,
                        "Content-Type": "application/json",
                        "Prefer": "return=minimal",
                    },
                    json=recording_data,
                )

                if resp.status_code not in (200, 201):
                    logger.error(
                        f"DB insert misslyckades ({resp.status_code}): "
                        f"{resp.text}"
                    )
                    return False

                logger.info(f"Metadata sparad i DB: {recording_id[:8]}")

                # 3. Ta bort lokal fil efter lyckad upload
                os.remove(file_path)
                logger.info(f"Lokal fil borttagen: {file_path}")

                return True

        except httpx.TimeoutException:
            logger.error("Upload timeout — försöker igen senare")
            return False
        except Exception as e:
            logger.error(f"Upload-fel: {e}")
            return False

    async def _refresh_token_if_needed(self):
        """Förnya access_token om den gått ut."""
        if self.access_token and time.time() < self.token_expires_at - 60:
            return

        if not self.refresh_token:
            logger.error("Ingen refresh_token — pairing krävs")
            return

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.supabase_url}/auth/v1/token?grant_type=refresh_token",
                    headers={
                        "apikey": self.anon_key,
                        "Content-Type": "application/json",
                    },
                    json={"refresh_token": self.refresh_token},
                )

                if resp.status_code == 200:
                    data = resp.json()
                    self.access_token = data["access_token"]
                    self.refresh_token = data["refresh_token"]
                    self.token_expires_at = time.time() + data.get(
                        "expires_in", 3600
                    )
                    logger.debug("Access token förnyad")

                    # Spara nya refresh_token
                    self._save_refresh_token(self.refresh_token)
                else:
                    logger.error(
                        f"Token-förnyelse misslyckades ({resp.status_code}): "
                        f"{resp.text}"
                    )

        except Exception as e:
            logger.error(f"Token-förnyelse fel: {e}")

    def _auth_headers(self) -> dict:
        """HTTP-headers med autentisering."""
        return {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.access_token or self.anon_key}",
        }

    def _generate_title(self, metadata: dict) -> str:
        """Generera en standardtitel baserad på tidpunkt."""
        try:
            recorded = datetime.fromisoformat(
                metadata["recorded_at"].replace("Z", "+00:00")
            )
            return recorded.strftime("Inspelning %d %b %Y, %H:%M")
        except (KeyError, ValueError):
            return "Inspelning"

    def _save_refresh_token(self, token: str):
        """Uppdatera refresh_token i credentials-filen."""
        creds_file = Path.home() / ".notepin_credentials.json"
        creds = {}
        if creds_file.exists():
            try:
                with open(creds_file, "r") as f:
                    creds = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        creds["refresh_token"] = token
        with open(creds_file, "w") as f:
            json.dump(creds, f, indent=2)

    async def _has_wifi(self) -> bool:
        """Kolla om Pi:n har WiFi-anslutning."""
        try:
            result = await asyncio.create_subprocess_exec(
                "iwgetid", "-r",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await result.communicate()
            return bool(stdout.strip())
        except Exception:
            return False
