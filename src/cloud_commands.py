"""Cloud Command Poller — hämtar kommandon från Supabase.

Används som fallback när appen inte är BLE-ansluten.
Pollar device_commands-tabellen för pending-kommandon.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Callable

import httpx

from src.utils.logger import setup_logger

logger = setup_logger("notepin.cloud_commands")


class CloudCommandPoller:
    """Pollar Supabase efter pending-kommandon för denna enhet."""

    def __init__(
        self,
        supabase_url: str,
        anon_key: str,
        access_token: str,
        device_id: str,
        poll_interval: int = 3,
    ):
        self.supabase_url = supabase_url
        self.anon_key = anon_key
        self.access_token = access_token
        self.device_id = device_id
        self.poll_interval = poll_interval

        self._running = False
        self._on_command: Callable | None = None

    def on_command(self, callback: Callable):
        """Registrera callback för mottagna kommandon.

        Callback får command-strängen som argument.
        """
        self._on_command = callback

    def update_token(self, access_token: str):
        """Uppdatera access token efter förnyelse."""
        self.access_token = access_token

    async def start(self):
        """Starta polling-loop."""
        self._running = True
        logger.info("Cloud command poller startad")

        while self._running:
            try:
                await self._check_commands()
            except Exception as e:
                logger.error(f"Cloud command poll-fel: {e}")

            await asyncio.sleep(self.poll_interval)

    async def stop(self):
        """Stoppa polling."""
        self._running = False
        logger.info("Cloud command poller stoppad")

    async def _check_commands(self):
        """Hämta och exekvera pending-kommandon."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Hämta pending-kommandon för denna enhet
                resp = await client.get(
                    f"{self.supabase_url}/rest/v1/device_commands",
                    headers=self._headers(),
                    params={
                        "device_id": f"eq.{self.device_id}",
                        "status": "eq.pending",
                        "order": "created_at.asc",
                        "limit": "5",
                    },
                )

                if resp.status_code != 200:
                    return

                commands = resp.json()

                for cmd in commands:
                    command = cmd.get("command")
                    cmd_id = cmd.get("id")

                    logger.info(f"Cloud-kommando mottaget: {command}")

                    # Exekvera kommandot
                    if self._on_command:
                        self._on_command(command)

                    # Markera som executed
                    await client.patch(
                        f"{self.supabase_url}/rest/v1/device_commands",
                        headers={
                            **self._headers(),
                            "Content-Type": "application/json",
                            "Prefer": "return=minimal",
                        },
                        params={"id": f"eq.{cmd_id}"},
                        json={
                            "status": "executed",
                            "executed_at": datetime.now(
                                timezone.utc
                            ).isoformat(),
                        },
                    )

        except httpx.TimeoutException:
            pass  # Tyst vid timeout — nästa poll tar det

    def _headers(self) -> dict:
        return {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.access_token}",
        }
