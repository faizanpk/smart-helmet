# led_handler.py – LED message indicator.
#
# On Raspberry Pi : controls an LED on LED_MSG_PIN via RPi.GPIO.
# On laptop       : prints state to the log (no physical LED).
#
# Wiring (Pi):
#   GPIO 5 (Pin 29) → 220 Ω resistor → LED anode (+)
#   LED cathode (−) → GND (Pin 30)
#
# Behaviour:
#   blink_alert()       – 5 fast flashes on message arrival (blocks briefly)
#   start_pending_blink() – slow background blink while messages are in queue
#   stop_pending_blink()  – stop background blink (queue empty)
#   on() / off()          – direct control

import platform
import threading
import time
import logging

log = logging.getLogger(__name__)

# ─── Platform detection ───────────────────────────────────────────────────────
_IS_PI = (platform.machine().startswith("aarch")
          or platform.machine().startswith("arm"))

_HAS_GPIO = False
_GPIO_mod = None

if _IS_PI:
    try:
        import RPi.GPIO as _GPIO_mod   # type: ignore
        _HAS_GPIO = True
    except ImportError:
        log.warning("[LED] RPi.GPIO not available – LED disabled.")

# ─── Module state ─────────────────────────────────────────────────────────────
_pin: int = 5                              # set by setup()
_blink_stop: threading.Event = threading.Event()
_blink_thread: threading.Thread | None = None
_blink_lock = threading.Lock()


# ─── Setup ────────────────────────────────────────────────────────────────────

def setup(pin: int) -> None:
    """Configure the LED GPIO pin as output (call once at startup)."""
    global _pin
    _pin = pin
    if _HAS_GPIO:
        _GPIO_mod.setup(pin, _GPIO_mod.OUT, initial=_GPIO_mod.LOW)
    log.info("[LED] Initialised on GPIO %d (Pi=%s).", pin, _HAS_GPIO)


# ─── Basic on / off ───────────────────────────────────────────────────────────

def on() -> None:
    if _HAS_GPIO:
        _GPIO_mod.output(_pin, _GPIO_mod.HIGH)
    else:
        log.debug("[LED] ON")


def off() -> None:
    if _HAS_GPIO:
        _GPIO_mod.output(_pin, _GPIO_mod.LOW)
    else:
        log.debug("[LED] OFF")


# ─── Alert blink (message arrived) ────────────────────────────────────────────

def blink_alert(times: int = 5, interval: float = 0.12) -> None:
    """
    Fast blink N times to alert the user that a new message arrived.
    Runs in a short background thread so it does not block the caller.
    """
    def _do():
        for _ in range(times):
            on()
            time.sleep(interval)
            off()
            time.sleep(interval)

    threading.Thread(target=_do, name="led-alert", daemon=True).start()
    if not _HAS_GPIO:
        log.info("[LED] BLINK ALERT (%d flashes)", times)


# ─── Pending blink (messages in queue) ───────────────────────────────────────

def start_pending_blink() -> None:
    """
    Start a slow background blink indicating messages are waiting.
    Safe to call multiple times – only one loop runs at a time.
    """
    global _blink_thread
    with _blink_lock:
        _blink_stop.set()           # stop any existing loop
        time.sleep(0.05)
        _blink_stop.clear()

        def _loop():
            while not _blink_stop.is_set():
                on()
                _wait(0.7)
                if _blink_stop.is_set():
                    break
                off()
                _wait(1.8)
            off()

        _blink_thread = threading.Thread(
            target=_loop, name="led-pending", daemon=True)
        _blink_thread.start()

    if not _HAS_GPIO:
        log.info("[LED] PENDING BLINK started.")


def stop_pending_blink() -> None:
    """Stop the background blink loop (call when queue becomes empty)."""
    _blink_stop.set()
    off()
    if not _HAS_GPIO:
        log.info("[LED] PENDING BLINK stopped.")


# ─── Helper ───────────────────────────────────────────────────────────────────

def _wait(seconds: float) -> None:
    """Interruptible sleep – checks stop event every 50 ms."""
    deadline = time.time() + seconds
    while time.time() < deadline and not _blink_stop.is_set():
        time.sleep(0.05)
