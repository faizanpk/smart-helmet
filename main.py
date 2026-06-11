# main.py – Smart Helmet Communication System – main entry point.
#
# Usage:
#   python main.py            (role from config.HELMET_ROLE)
#   python main.py manager    (override role at runtime)
#   python main.py worker     (override role at runtime)
#
# Architecture:
#   • One background thread per button (each blocks on wait_for_press).
#   • Background threads: reminder loop, network receiver, speaker-mode monitor.
#   • All user feedback is delivered via TTS (no UI).
#
# Manager laptop keyboard controls (when not on Pi):
#   SPACE  – hold to talk (translation) OR hold to mute during live call
#   r      – hold to record reminder
#   h      – handover (play or record)
#   L      – simulate toggle switch (start/stop live call)

import sys
import logging
import threading
import time

# ─── Override role from command-line argument ─────────────────────────────────
if len(sys.argv) > 1 and sys.argv[1] in ("manager", "worker"):
    import config
    config.HELMET_ROLE = sys.argv[1]

import config
import db
import gpio_handler as gpio
import network
import speaker_mode as spk_module
from live_call import LiveCall
from reminders import start_reminder_loop, record_and_save_reminder
from handover import handle_handover_button
from translation import (
    play_audio_bytes, speak, translation_pipeline,
    record_until_release, setup_offline_models,
)

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-8s]  %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Network message handler (runs in network receive thread)
# ─────────────────────────────────────────────────────────────────────────────

def _on_network_message(msg_type: str, meta: dict, payload: bytes) -> None:
    """Dispatch incoming messages from the partner helmet."""
    if msg_type == network.MSG_TRANSLATION:
        log.info("[MAIN] Incoming translation audio (%d bytes) – playing.", len(payload))
        # Play in its own thread so the receive loop is not blocked.
        threading.Thread(target=play_audio_bytes, args=(payload,), daemon=True).start()
    elif msg_type == network.MSG_CONTROL:
        log.info("[MAIN] Control message: %s", meta)
    else:
        log.warning("[MAIN] Unknown message type: %s", msg_type)


# ─────────────────────────────────────────────────────────────────────────────
# Button handlers
# ─────────────────────────────────────────────────────────────────────────────

# Module-level LiveCall instance (created in main(), referenced by _handle_speak)
_live_call: LiveCall | None = None


def _handle_speak() -> None:
    """SPEAK button:
       • During a live call   → hold = MUTE, release = UNMUTE
       • Normal mode          → hold to talk, releases sends translated audio.
    """
    global _live_call
    log.info("[BTN] SPEAK pressed")

    if _live_call and _live_call.is_active:
        # Live call is active – button = push-to-mute
        speak("Muted.", config.HELMET_LANGUAGE_CODE)
        _live_call.mute(True)
        is_held = lambda: gpio.is_pressed(config.BTN_SPEAK)
        while is_held():
            time.sleep(0.05)
        _live_call.mute(False)
        speak("Unmuted.", config.HELMET_LANGUAGE_CODE)
        return

    # Normal PTT translation mode
    speak("Recording.", config.HELMET_LANGUAGE_CODE)

    is_held = lambda: gpio.is_pressed(config.BTN_SPEAK)
    audio   = record_until_release(is_held)

    if len(audio) < config.CHUNK * 4:   # < ~0.25 s  → probably accidental
        speak("Too short. Please hold the button while speaking.", config.HELMET_LANGUAGE_CODE)
        return

    speak("Sending.", config.HELMET_LANGUAGE_CODE)
    original, translated, mp3 = translation_pipeline(audio)

    if not original:
        speak("Could not understand. Please try again.", config.HELMET_LANGUAGE_CODE)
        return

    log.info("[MAIN] Translated: '%s' -> '%s'", original, translated)
    ok = network.send_audio(mp3, meta={"from": config.HELMET_ROLE})
    if ok:
        speak("Message sent.", config.HELMET_LANGUAGE_CODE)
    else:
        speak("Partner not reachable. Message not sent.", config.HELMET_LANGUAGE_CODE)


def _handle_reminder() -> None:
    """Reminder button: record voice reminder with a spoken time cue."""
    log.info("[BTN] REMINDER pressed")
    is_held = lambda: gpio.is_pressed(config.BTN_REMINDER)
    record_and_save_reminder(is_held)


def _handle_handover() -> None:
    """Handover button: play unread message OR record a new one."""
    log.info("[BTN] HANDOVER pressed")
    is_held = lambda: gpio.is_pressed(config.BTN_HANDOVER)
    handle_handover_button(is_held)


# ─────────────────────────────────────────────────────────────────────────────
# Button monitor threads
# ─────────────────────────────────────────────────────────────────────────────

def _button_monitor(pin: int, handler, name: str) -> None:
    """
    Continuously wait for a button press, call handler(), then repeat.
    Each button runs in its own daemon thread.
    A short sleep after handling prevents double-trigger on release bounce.
    """
    log.info("[MONITOR] %s monitor started (pin %d).", name, pin)
    while True:
        gpio.wait_for_press(pin)
        try:
            handler()
        except Exception as exc:
            log.error("[MONITOR] Error in %s handler: %s", name, exc)
        time.sleep(0.4)   # debounce / cooldown


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 60)
    log.info("  Smart Helmet – Communication Module")
    log.info("  Role     : %s", config.HELMET_ROLE)
    log.info("  Language : %s → %s", config.HELMET_LANGUAGE_CODE, config.TARGET_LANGUAGE_CODE)
    log.info("  Partner  : %s:%d", config.PARTNER_IP, config.COMM_PORT)
    log.info("=" * 60)

    # 1. Download / verify offline models (Whisper + argostranslate)
    #    Needs internet on first run; instant on subsequent runs.
    log.info("[MAIN] Checking offline models…")
    setup_offline_models()

    # 2. Initialise database
    db.init_db()

    # 3. Initialise GPIO (or keyboard fallback)
    gpio.setup_gpio()

    # 4. Live call engine + speaker mode monitor
    global _live_call
    _live_call = LiveCall(config.PARTNER_IP)
    speaker_manager = spk_module.SpeakerModeManager(config.SWITCH_SPEAKER)
    speaker_manager.set_live_call(_live_call)
    speaker_manager.start()

    # 5. Reminder background loop
    start_reminder_loop()

    # 6. Network – role determines server vs client
    if config.HELMET_ROLE == "manager":
        network.start_server(config.COMM_PORT, _on_network_message)
        log.info("[MAIN] Manager: TCP server started on port %d.", config.COMM_PORT)
        if not gpio.IS_PI:
            log.info("[MAIN] Keyboard controls: SPACE=speak/mute  r=reminder  h=handover  L=live-call")
    else:
        network.connect_to_server(config.PARTNER_IP, config.COMM_PORT, _on_network_message)
        log.info("[MAIN] Worker: connecting to manager at %s:%d.", config.PARTNER_IP, config.COMM_PORT)

    # 7. Start button monitor threads
    monitors = [
        threading.Thread(
            target=_button_monitor,
            args=(config.BTN_SPEAK,    _handle_speak,    "SPEAK"),
            name="btn-speak",
            daemon=True,
        ),
        threading.Thread(
            target=_button_monitor,
            args=(config.BTN_REMINDER, _handle_reminder, "REMINDER"),
            name="btn-reminder",
            daemon=True,
        ),
        threading.Thread(
            target=_button_monitor,
            args=(config.BTN_HANDOVER, _handle_handover, "HANDOVER"),
            name="btn-handover",
            daemon=True,
        ),
    ]
    for t in monitors:
        t.start()

    # 7. Announce readiness
    log.info("[MAIN] System ready.")
    speak("Smart helmet ready.", config.HELMET_LANGUAGE_CODE)

    # 8. Keep main thread alive (daemon threads stop if main exits)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("[MAIN] Shutdown requested.")
    finally:
        gpio.cleanup()
        log.info("[MAIN] Goodbye.")


if __name__ == "__main__":
    main()
