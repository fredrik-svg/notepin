"""LED-kontroll för statusindikation.

Färgkoder:
  - Grönt blink:   Redo (standby)
  - Rött fast:     Inspelning pågår
  - Rött blink:    Highlight markerad
  - Blått blink:   Upload pågår
  - Gult blink:    BLE pairing-läge
  - Rött snabbt:   Fel
  - Av:            Ström sparas (deep idle)
"""

import threading
import time

from src.utils.logger import setup_logger

logger = setup_logger("notepin.led")

try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False


class LEDState:
    OFF = "off"
    STANDBY = "standby"
    RECORDING = "recording"
    HIGHLIGHT = "highlight"
    UPLOADING = "uploading"
    PAIRING = "pairing"
    ERROR = "error"
    LOW_BATTERY = "low_battery"


# Färg-mappning: (R, G, B) — 1 = på, 0 = av
LED_COLORS = {
    LEDState.OFF: (0, 0, 0),
    LEDState.STANDBY: (0, 1, 0),
    LEDState.RECORDING: (1, 0, 0),
    LEDState.HIGHLIGHT: (1, 0, 0),
    LEDState.UPLOADING: (0, 0, 1),
    LEDState.PAIRING: (1, 1, 0),
    LEDState.ERROR: (1, 0, 0),
    LEDState.LOW_BATTERY: (1, 0.3, 0),
}

# Blink-mönster: (on_time, off_time) i sekunder. None = fast ljus.
LED_PATTERNS = {
    LEDState.OFF: None,
    LEDState.STANDBY: (0.1, 3.0),        # Kort blink var 3:e sekund
    LEDState.RECORDING: None,              # Fast rött
    LEDState.HIGHLIGHT: (0.1, 0.1),        # Snabbt blink 3 gånger, sedan fast
    LEDState.UPLOADING: (0.5, 0.5),        # Blått blink
    LEDState.PAIRING: (0.3, 0.3),          # Gult blink
    LEDState.ERROR: (0.1, 0.1),            # Snabbt rött blink
    LEDState.LOW_BATTERY: (0.2, 2.0),      # Långsamt orange blink
}


class LEDController:
    """Hanterar RGB LED för statusindikation."""

    def __init__(self, config: dict):
        gpio_cfg = config["gpio"]
        self.led_pin = gpio_cfg.get("led_pin", 18)
        self.led_type = gpio_cfg.get("led_type", "simple")

        self._state = LEDState.OFF
        self._running = False
        self._thread: threading.Thread | None = None
        self._highlight_count = 0

        # Simple RGB LED: tre GPIO-pins (R, G, B)
        # Vi använder pin, pin+1, pin+2 som R, G, B
        self._pins = {
            "r": self.led_pin,
            "g": self.led_pin + 1,
            "b": self.led_pin + 2,
        }

    def start(self):
        """Starta LED-kontroller."""
        if not HAS_GPIO:
            logger.info("GPIO ej tillgängligt — LED simuleras via logg")
            self._running = True
            return

        GPIO.setmode(GPIO.BCM)
        for pin in self._pins.values():
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)

        self._running = True
        self._thread = threading.Thread(
            target=self._blink_loop,
            daemon=True,
            name="led",
        )
        self._thread.start()

        logger.info("LED-kontroller startad")

    def stop(self):
        """Stoppa LED och rensa GPIO."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

        self._set_color(0, 0, 0)

        if HAS_GPIO:
            for pin in self._pins.values():
                try:
                    GPIO.cleanup(pin)
                except Exception:
                    pass

    def set_state(self, state: str):
        """Ändra LED-tillstånd."""
        if state != self._state:
            self._state = state
            logger.debug(f"LED: {state}")

    def flash_highlight(self):
        """Kort highlight-blink (3 snabba blink)."""
        self._highlight_count = 3

    def _blink_loop(self):
        """Huvudloop för LED-blinkning."""
        while self._running:
            # Hantera highlight-blink (överridear normalt mönster)
            if self._highlight_count > 0:
                color = LED_COLORS[LEDState.HIGHLIGHT]
                self._set_color(*color)
                time.sleep(0.1)
                self._set_color(0, 0, 0)
                time.sleep(0.1)
                self._highlight_count -= 1
                continue

            color = LED_COLORS.get(self._state, (0, 0, 0))
            pattern = LED_PATTERNS.get(self._state)

            if pattern is None:
                # Fast ljus
                self._set_color(*color)
                time.sleep(0.1)
            else:
                on_time, off_time = pattern
                self._set_color(*color)
                time.sleep(on_time)
                self._set_color(0, 0, 0)
                time.sleep(off_time)

    def _set_color(self, r: float, g: float, b: float):
        """Sätt RGB-färg. Värden 0-1."""
        if not HAS_GPIO:
            return

        try:
            GPIO.output(self._pins["r"], GPIO.HIGH if r > 0.5 else GPIO.LOW)
            GPIO.output(self._pins["g"], GPIO.HIGH if g > 0.5 else GPIO.LOW)
            GPIO.output(self._pins["b"], GPIO.HIGH if b > 0.5 else GPIO.LOW)
        except Exception:
            pass
