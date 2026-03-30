"""GPIO-knapphantering med kort och långt tryck.

Kort tryck (<1s):
  - Om inspelning pågår: lägg till highlight
  - Om ingen inspelning: ignorera (förhindra oavsiktlig start)

Långt tryck (>1s):
  - Starta eller stoppa inspelning

Debouncing ingår för att filtrera bort kontaktstudsar.
"""

import time
import threading
from typing import Callable

from src.utils.logger import setup_logger

logger = setup_logger("notepin.button")

# Försök importera GPIO (finns bara på Pi)
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False
    logger.warning("RPi.GPIO ej tillgängligt — knapphantering inaktiverad")

# Tidsgränser i sekunder
LONG_PRESS_THRESHOLD = 1.0
DEBOUNCE_TIME = 0.05


class ButtonHandler:
    """Hanterar GPIO-knapp med kort/långt tryck."""

    def __init__(self, config: dict):
        self.pin = config["gpio"]["button_pin"]
        self._on_short_press: Callable | None = None
        self._on_long_press: Callable | None = None
        self._press_start: float = 0
        self._running = False

    def on_short_press(self, callback: Callable):
        """Registrera callback för kort knapptryck (highlight)."""
        self._on_short_press = callback

    def on_long_press(self, callback: Callable):
        """Registrera callback för långt knapptryck (start/stopp)."""
        self._on_long_press = callback

    def start(self):
        """Starta GPIO-övervakning."""
        if not HAS_GPIO:
            logger.warning("GPIO ej tillgängligt — simuleringsläge")
            return

        self._running = True

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # Interrupt-baserad detektering
        GPIO.add_event_detect(
            self.pin,
            GPIO.BOTH,
            callback=self._gpio_callback,
            bouncetime=int(DEBOUNCE_TIME * 1000),
        )

        logger.info(f"Knapphantering startad på GPIO{self.pin}")

    def stop(self):
        """Stoppa GPIO-övervakning och rensa resurser."""
        self._running = False
        if HAS_GPIO:
            try:
                GPIO.remove_event_detect(self.pin)
                GPIO.cleanup(self.pin)
            except Exception:
                pass
        logger.info("Knapphantering stoppad")

    def _gpio_callback(self, channel):
        """Callback från GPIO-interrupt."""
        if not self._running:
            return

        # Knappen är aktiv låg (PUD_UP)
        if GPIO.input(channel) == GPIO.LOW:
            # Knappen trycktes ned
            self._press_start = time.time()
        else:
            # Knappen släpptes
            if self._press_start == 0:
                return

            duration = time.time() - self._press_start
            self._press_start = 0

            if duration < DEBOUNCE_TIME:
                return

            # Kör callback i egen tråd så vi inte blockerar GPIO
            if duration >= LONG_PRESS_THRESHOLD:
                logger.info(f"Långt tryck ({duration:.1f}s)")
                if self._on_long_press:
                    threading.Thread(
                        target=self._on_long_press,
                        daemon=True,
                    ).start()
            else:
                logger.info(f"Kort tryck ({duration:.1f}s)")
                if self._on_short_press:
                    threading.Thread(
                        target=self._on_short_press,
                        daemon=True,
                    ).start()


class SimulatedButton:
    """Simulerad knapp för utveckling utan GPIO (keyboard-input)."""

    def __init__(self):
        self._on_short_press: Callable | None = None
        self._on_long_press: Callable | None = None
        self._running = False

    def on_short_press(self, callback: Callable):
        self._on_short_press = callback

    def on_long_press(self, callback: Callable):
        self._on_long_press = callback

    def start(self):
        self._running = True
        thread = threading.Thread(target=self._input_loop, daemon=True)
        thread.start()
        logger.info(
            "Simulerad knapp aktiv — "
            "tryck ENTER för highlight, 'r' + ENTER för start/stopp"
        )

    def stop(self):
        self._running = False

    def _input_loop(self):
        while self._running:
            try:
                cmd = input()
                if cmd.lower() == "r":
                    if self._on_long_press:
                        self._on_long_press()
                else:
                    if self._on_short_press:
                        self._on_short_press()
            except EOFError:
                break
