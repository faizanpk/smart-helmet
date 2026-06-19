# gpio_handler.py – GPIO button and LED management.
#
# On Raspberry Pi  → uses RPi.GPIO (BCM mode, active-LOW buttons with pull-ups)
# On laptop (manager or worker) → uses the `keyboard` library to simulate GPIO:
#
# Manager (Laptop 1):
#   SPACE → BTN_SPEAK_MANAGER    (hold to send PTT to selected worker)
#   F1    → BTN_CALL_W01         (call/answer/end Worker A)
#   F2    → BTN_CALL_W02         (call/answer/end Worker B)
#   p     → BTN_PLAY_MSG         (short press = play, hold 3 s = language config)
#   r     → BTN_REMINDER
#   h     → BTN_HANDOVER
#   1/2/3 → worker PTT selection (handled in main.py)
#
# Worker (Laptop 2):
#   SPACE → BTN_SPEAK_MANAGER    (hold to send PTT to manager)
#   w     → BTN_SPEAK_WORKER     (hold to send PTT to peer worker)
#   c     → BTN_CALL_MANAGER     (call/answer/end manager)
#   p     → BTN_PLAY_MSG
#   r     → BTN_REMINDER
#   h     → BTN_HANDOVER

import threading
import time
import logging
import config

log = logging.getLogger(__name__)

# ─── Platform detection ───────────────────────────────────────────────────────

IS_PI = False
try:
    import RPi.GPIO as GPIO          # type: ignore
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    IS_PI = True
    log.info("[GPIO] Raspberry Pi detected – using RPi.GPIO")
except (ImportError, RuntimeError):
    log.info("[GPIO] Not on Pi – keyboard fallback active")

HAS_KEYBOARD = False
_kb = None
if not IS_PI:
    try:
        import keyboard as _kb       # type: ignore
        HAS_KEYBOARD = True
    except ImportError:
        log.warning("[GPIO] 'keyboard' package not found – run:  pip install keyboard")

# ─── Pin → key mapping ────────────────────────────────────────────────────────
# BTN_CALL_W01 and BTN_CALL_W02 are None (manager-only, handled via keyboard hooks
# in main.py directly).  All physical Pi buttons are in this map.
_KEY_MAP = {
    config.BTN_SPEAK_MANAGER: "space",
    config.BTN_SPEAK_WORKER:  "w",
    config.BTN_REMINDER:      "r",
    config.BTN_HANDOVER:      "h",
    config.BTN_PLAY_MSG:      "p",
    config.BTN_CALL_MANAGER:  "c",   # worker: call/answer/end manager
}


# ─── Initialisation ───────────────────────────────────────────────────────────

def setup_gpio() -> None:
    """Initialise all button pins (inputs w/ pull-ups) and LED pin (output)."""
    if IS_PI:
        # Input buttons – active LOW
        for pin in (config.BTN_SPEAK_MANAGER, config.BTN_SPEAK_WORKER,
                    config.BTN_REMINDER, config.BTN_HANDOVER,
                    config.BTN_PLAY_MSG, config.BTN_CALL_MANAGER):
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        # LED output
        GPIO.setup(config.LED_MSG_PIN, GPIO.OUT, initial=GPIO.LOW)
        log.info(
            "[GPIO] Pins: SPEAK_MGR=%d SPEAK_WKR=%d REMINDER=%d HANDOVER=%d "
            "PLAY=%d CALL_MGR=%d LED=%d",
            config.BTN_SPEAK_MANAGER, config.BTN_SPEAK_WORKER,
            config.BTN_REMINDER, config.BTN_HANDOVER,
            config.BTN_PLAY_MSG, config.BTN_CALL_MANAGER, config.LED_MSG_PIN,
        )
    else:
        log.info(
            "[GPIO] Keyboard sim: SPACE=PTT-manager  w=PTT-worker  "
            "c=call-manager  p=play  r=reminder  h=handover"
        )


# ─── Button state ─────────────────────────────────────────────────────────────

def is_pressed(pin: int) -> bool:
    """Return True while the button is currently held (active LOW on Pi)."""
    if IS_PI:
        return GPIO.input(pin) == GPIO.LOW
    if HAS_KEYBOARD:
        key = _KEY_MAP.get(pin)
        return _kb.is_pressed(key) if key else False
    return False


def wait_for_press(pin: int, timeout: float | None = None) -> bool:
    """
    Block until the button is pressed.
    Returns True when pressed, False on timeout (or if no keyboard lib).
    """
    if IS_PI:
        event = threading.Event()

        def _cb(_ch):
            event.set()

        GPIO.add_event_detect(pin, GPIO.FALLING, callback=_cb, bouncetime=200)
        result = event.wait(timeout)
        GPIO.remove_event_detect(pin)
        return result

    # Keyboard fallback
    if HAS_KEYBOARD:
        key = _KEY_MAP.get(pin, "")
        if timeout is None:
            _kb.wait(key)
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _kb.is_pressed(key):
                return True
            time.sleep(0.05)
        return False

    log.error("[GPIO] No input method available – cannot wait for button press.")
    return False


def wait_for_release(pin: int, max_seconds: float = 60.0) -> float:
    """
    Block until the button is released (or max_seconds exceeded).
    Returns the duration the button was held in seconds.
    """
    start = time.time()
    deadline = start + max_seconds
    if IS_PI:
        while GPIO.input(pin) == GPIO.LOW and time.time() < deadline:
            time.sleep(0.02)
    elif HAS_KEYBOARD:
        key = _KEY_MAP.get(pin, "")
        while _kb.is_pressed(key) and time.time() < deadline:
            time.sleep(0.02)
    return time.time() - start


# ─── Cleanup ──────────────────────────────────────────────────────────────────

def cleanup() -> None:
    """Release GPIO resources on shutdown."""
    if IS_PI:
        GPIO.cleanup()
        log.info("[GPIO] GPIO cleanup done.")
