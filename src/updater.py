"""OTA-uppdatering via git pull.

Kontrollerar vid boot om det finns ny kod på GitHub.
Om uppdatering hittas, restarta notepin-tjänsten.
"""

import subprocess
from pathlib import Path

from src.utils.logger import setup_logger

logger = setup_logger("notepin.updater")

# Projektroten (där .git finns)
PROJECT_ROOT = Path(__file__).parent.parent


def check_for_updates() -> bool:
    """Kontrollera och tillämpa uppdateringar från GitHub.

    Returns:
        True om uppdatering tillämpades (omstart behövs)
    """
    try:
        # Hämta senaste ändringar
        result = subprocess.run(
            ["git", "fetch", "--quiet"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            logger.warning(f"git fetch misslyckades: {result.stderr}")
            return False

        # Kolla om vi ligger bakom origin
        result = subprocess.run(
            ["git", "status", "-uno", "--porcelain", "-b"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if "behind" not in result.stdout:
            logger.info("Ingen uppdatering tillgänglig")
            return False

        # Tillämpa uppdateringar
        logger.info("Ny version hittad — uppdaterar...")

        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            logger.error(f"git pull misslyckades: {result.stderr}")
            return False

        logger.info(f"Uppdaterad: {result.stdout.strip()}")

        # Installera eventuella nya Python-beroenden
        req_file = PROJECT_ROOT / "requirements.txt"
        if req_file.exists():
            subprocess.run(
                ["pip", "install", "-r", str(req_file), "--quiet",
                 "--break-system-packages"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                timeout=120,
            )

        return True

    except subprocess.TimeoutExpired:
        logger.error("Uppdateringscheck timeout")
        return False
    except Exception as e:
        logger.error(f"Uppdateringsfel: {e}")
        return False


def restart_service():
    """Starta om notepin systemd-tjänsten."""
    logger.info("Startar om notepin-tjänsten...")
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "notepin"],
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Omstart misslyckades: {e}")
