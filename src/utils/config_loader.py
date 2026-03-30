"""Laddar och validerar YAML-konfigurationen."""

import os
import yaml
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.yaml"


def load_config(path: str | Path | None = None) -> dict:
    """Ladda konfiguration från YAML-fil.

    Letar i ordning:
    1. Angiven path
    2. Miljövariabel NOTEPIN_CONFIG
    3. config/config.yaml relativt till projektroten
    """
    if path is None:
        path = os.environ.get("NOTEPIN_CONFIG", str(DEFAULT_CONFIG_PATH))

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Konfigurationsfil saknas: {path}\n"
            f"Kopiera config.example.yaml:\n"
            f"  cp config/config.example.yaml config/config.yaml"
        )

    with open(path, "r") as f:
        config = yaml.safe_load(f)

    _validate(config)
    return config


def _validate(config: dict) -> None:
    """Validera att nödvändiga nycklar finns."""
    required = [
        ("supabase", "url"),
        ("supabase", "anon_key"),
        ("supabase", "storage_bucket"),
        ("audio", "sample_rate"),
        ("ble", "device_name"),
        ("gpio", "button_pin"),
    ]

    for keys in required:
        obj = config
        for key in keys:
            if not isinstance(obj, dict) or key not in obj:
                raise ValueError(
                    f"Saknad konfiguration: {'.'.join(keys)}"
                )
            obj = obj[key]

        if obj is None or (isinstance(obj, str) and obj.startswith("YOUR")):
            raise ValueError(
                f"Konfigurera {'.'.join(keys)} i config.yaml"
            )


def get_device_serial() -> str:
    """Läs Pi:ns unika CPU-serienummer från /proc/cpuinfo."""
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.strip().split(":")[1].strip()
    except (FileNotFoundError, IndexError):
        pass

    # Fallback: generera från MAC-adress
    import uuid
    return f"fallback-{uuid.getnode():012x}"
