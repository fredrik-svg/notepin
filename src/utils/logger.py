"""Loggning till fil och stdout (systemd journal)."""

import logging
import sys
from pathlib import Path


def setup_logger(
    name: str = "notepin",
    log_file: str | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Konfigurera logger med fil- och stdout-handler."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Stdout — fångas av systemd journal
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)

    # Fil (valfritt)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
