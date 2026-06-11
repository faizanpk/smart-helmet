# gpio_handler.py – GPIO button and toggle-switch management.
#
# On Raspberry Pi  → uses RPi.GPIO (BCM mode, active-LOW buttons with pull-ups)
# On manager laptop → uses the `keyboard` library to simulate buttons:
#       SPACE  = BTN_SPEAK   (hold to talk / hold to mute during live call)
#       r      = BTN_REMINDER
#       h      = BTN_HANDOVER
#       l      = SWITCH_SPEAKER toggle (press once to start live call,
#                                       press again to end it)

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
    log.info("[GPIO] Not on Pi – keyboard fallback active (SPACE / r / h)")

HAS_KEYBOARD = False
_kb = None
if not IS_PI:
    try:
        import keyboard as _kb       # type: ignore
        HAS_KEYBOARD = True
    except ImportError:
        log.warning("[GPIO] 'keyboard' package not found – run:  pip install keyboard")

# Map GPIO pin → keyboard key
_KEY_MAP = {
    config.BTN_SPEAK:      "space",
    config.BTN_REMINDER:   "r",
    config.BTN_HANDOVER:   "h",
}

# Simulated toggle switch state (toggled by pressing 'l' on the laptop)
_switch_state = False

def _on_l_key(event):
    """Toggle the simulated switch state each time 'l' is pressed."""
    global _switch_state
    if event.event_type == "down":
        _switch_state = not _switch_state
        log.info("[GPIO] Simulated toggle switch: %s", "ON" if _switch_state else "OFF")


# ─── Initialisation ───────────────────────────────────────────────────────────

def setup_gpio():
    """Initialise all button pins with internal pull-ups (active LOW)."""
    if IS_PI:
        for pin in (config.BTN_SPEAK, config.BTN_REMINDER,
                    config.BTN_HANDOVER, config.SWITCH_SPEAKER):
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        log.info("[GPIO] Pins configured: SPEAK=%d REMINDER=%d HANDOVER=%d SWITCH=%d",
                 config.BTN_SPEAK, config.BTN_REMINDER,
                 config.BTN_HANDOVER, config.SWITCH_SPEAKER)
    else:
        if HAS_KEYBOARD:
            _kb.hook_key("l", _on_l_key)
        log.info("[GPIO] Keyboard simulation ready – SPACE=SPEAK  r=REMINDER  h=HANDOVER  l=TOGGLE")


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


# ─── Toggle switch ────────────────────────────────────────────────────────────

def get_switch_state(pin: int) -> bool:
    """
    Return True if the toggle switch is ON.
    The 3-pin ON-OFF-ON switch is wired so ON pulls the pin HIGH.
    On laptop: press 'l' to toggle between ON and OFF.
    """
    if IS_PI:
        return GPIO.input(pin) == GPIO.HIGH
    return _switch_state


# ─── Cleanup ──────────────────────────────────────────────────────────────────

def cleanup():
    """Release GPIO resources on shutdown."""
    if IS_PI:
        GPIO.cleanup()
        log.info("[GPIO] GPIO cleanup done.")
