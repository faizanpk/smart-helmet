# main.py – Smart Helmet Communication System – main entry point.
#
# Usage:
#   python main.py            (role from config.HELMET_ROLE)
#   python main.py manager    (override role at runtime)
#   python main.py worker     (override role at runtime)
#
# ─── Manager keyboard controls ─────────────────────────────────────────────────
#   1 / 2   – select target worker for PTT
#   Hold SPACE  – record & send PTT message to selected worker
#   F1          – call/answer/end call with Worker A (w-01 / Pi)
#   F2          – call/answer/end call with Worker B (w-02 / Laptop)
#   p           – short press: play message | hold 3 s: language config
#   r           – hold: record reminder
#   h           – play/record handover
#   Hold SPACE during call – mute yourself
#
# ─── Worker Pi / Laptop 2 controls ────────────────────────────────────────────
#   Hold SPACE  – record & send PTT to manager
#   Hold w      – record & send PTT to peer worker (no call between workers)
#   c           – call/answer/end call with manager
#   p           – short press: play message | hold 3 s: language config
#   r           – hold: record reminder
#   h           – play/record handover
#   Hold SPACE during call – mute yourself

import sys
import logging
import threading
import time
from typing import Optional, Dict

# ─── Override role from command-line ─────────────────────────────────────────
if len(sys.argv) > 1 and sys.argv[1] in ("manager", "worker"):
    import config
    config.HELMET_ROLE = sys.argv[1]

import config
import db
import gpio_handler as gpio
import led_handler
import message_store
import network
import peer_network
from call_manager import CallSlot
from live_call import LiveCall
from reminders import start_reminder_loop, record_and_save_reminder, replay_last_reminder
from handover import handle_handover_button, record_handover
from translation import (
    play_audio_bytes, speak, translate_text, text_to_speech,
    transcribe_only, play_alert_beep, voice_to_text, t,
    parse_config_command, load_language_setting,
    save_language_setting, apply_language_setting,
    record_until_release, setup_offline_models,
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-8s]  %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


_last_action_time: Dict[int, float] = {}
DOUBLE_TAP_WINDOW = 2.0   # seconds


def _is_double_tap(pin: int) -> bool:
    """True if this button was also pressed within the last DOUBLE_TAP_WINDOW seconds."""
    now = time.time()
    last = _last_action_time.get(pin, 0)
    _last_action_time[pin] = now
    return (now - last) <= DOUBLE_TAP_WINDOW


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_peer_worker_ip() -> str:
    """
    For a worker helmet, return the IP of the OTHER worker (not itself).
    Looks up config.WORKER_IPS and skips the entry matching our own HELMET_ID.
    """
    for wid, ip in config.WORKER_IPS.items():
        if wid != config.HELMET_ID:
            return ip
    return ""

def _preview_text(text: str, max_words: int = 6) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def _lang_code(short: str) -> str:
    return "en-US" if short == "en" else "de-DE"


def _make_live_call(partner_ip: str, port_offset: int = 0):
    """Factory that creates and starts a LiveCall for the given partner."""
    lc = LiveCall()
    lc.start(partner_ip, port_offset)
    return lc


# ─────────────────────────────────────────────────────────────────────────────
# Call slots  (built in main() after config is loaded)
# ─────────────────────────────────────────────────────────────────────────────
#
# Manager has two slots – one per worker:
#   _call_slots["w-01"]  ← F1 key
#   _call_slots["w-02"]  ← F2 key
#
# Worker has one slot – to the manager:
#   _call_slots["manager"]  ← c key / BTN_CALL_MANAGER
#
_call_slots: Dict[str, CallSlot] = {}


def _get_slot_for_worker(worker_id: str) -> Optional[CallSlot]:
    return _call_slots.get(worker_id)


def _get_manager_slot() -> Optional[CallSlot]:
    return _call_slots.get("manager")


# ─────────────────────────────────────────────────────────────────────────────
# Manager – PTT worker selection state
# ─────────────────────────────────────────────────────────────────────────────

_selected_worker_id: Optional[str] = None
_selection_lock = threading.Lock()


def _select_worker(index: int) -> None:
    global _selected_worker_id

    if index < 1 or index > len(config._FIXED_WORKER_ORDER):
        speak(t("only_n_workers", n=len(config._FIXED_WORKER_ORDER)), config.HELMET_LANGUAGE_CODE)
        return
    
    target_id = config._FIXED_WORKER_ORDER[index - 1]
    connected = network.get_worker_ids()

    if target_id not in connected:
        speak(t("only_n_workers", n=index), config.HELMET_LANGUAGE_CODE)
        return

    with _selection_lock:
        _selected_worker_id = target_id
    log.info("[MAIN] PTT target set to '%s'.", _selected_worker_id)
    speak(t("talking_to_worker", index=index), config.HELMET_LANGUAGE_CODE)


# ─────────────────────────────────────────────────────────────────────────────
# Network message handlers  (called from receive threads)
# ─────────────────────────────────────────────────────────────────────────────

def _handle_incoming_voice_message(meta: dict, channel: str = "manager") -> None:
    sender_id   = meta.get("sender_id",   "unknown")
    sender_role = meta.get("sender_role", "unknown")
    text        = meta.get("text",        "")
    language    = meta.get("language",    "en")
    is_emergency = meta.get("is_emergency", False)

    if not text:
        log.warning("[MAIN] Empty voice message from '%s'.", sender_id)
        return
    
    if is_emergency:
        _handle_emergency_incoming(meta)
        return

    message_store.push(sender_id, sender_role, text, language, channel)

    threading.Thread(target=play_alert_beep, args=(3,),
                     daemon=True, name="beep").start()
    led_handler.blink_alert()
    led_handler.start_pending_blink()

    n = message_store.count()
    speak(t("n_messages_received", n=n), config.HELMET_LANGUAGE_CODE)


def _on_network_message(msg_type: str, meta: dict, payload: bytes) -> None:
    """Dispatch incoming TCP messages (manager server ↔ worker client)."""
    if msg_type == network.MSG_VOICE_MESSAGE:
        _handle_incoming_voice_message(meta, channel="manager")

    elif msg_type == network.MSG_CALL_REQUEST:
        sender_id = meta.get("sender_id", "")
        slot = (_get_slot_for_worker(sender_id)   # manager receives from worker
                if config.HELMET_ROLE == "manager"
                else _get_manager_slot())           # worker receives from manager
        if slot:
            slot.on_call_request_received()
        else:
            log.warning("[MAIN] CALL_REQUEST from unknown sender '%s'.", sender_id)

    elif msg_type == network.MSG_CALL_ACCEPT:
        sender_id = meta.get("sender_id", "")
        slot = (_get_slot_for_worker(sender_id)
                if config.HELMET_ROLE == "manager"
                else _get_manager_slot())
        if slot:
            slot.on_call_accepted()

    elif msg_type == network.MSG_CALL_END:
        sender_id = meta.get("sender_id", "")
        slot = (_get_slot_for_worker(sender_id)
                if config.HELMET_ROLE == "manager"
                else _get_manager_slot())
        if slot:
            slot.on_call_ended()

    elif msg_type == network.MSG_TRANSLATION:
        # Legacy raw audio from live_call (already handled by LiveCall threads)
        pass

    elif msg_type == network.MSG_EMERGENCY:
        _handle_emergency_incoming(meta)

    elif msg_type == network.MSG_CONTROL:
        log.info("[MAIN] Control: %s", meta)

    else:
        log.warning("[MAIN] Unknown msg_type '%s'.", msg_type)


def _on_peer_message(msg_type: str, meta: dict, payload: bytes) -> None:
    """Dispatch incoming peer (worker-to-worker) messages."""
    if msg_type == "voice_message":
        _handle_incoming_voice_message(meta, channel="peer")
    elif msg_type == "translation":
        threading.Thread(target=play_audio_bytes, args=(payload,),
                         daemon=True).start()
    else:
        log.warning("[MAIN] Unknown peer msg_type '%s'.", msg_type)


# ─────────────────────────────────────────────────────────────────────────────
# Manager connection events
# ─────────────────────────────────────────────────────────────────────────────


def _on_manager_disconnected() -> None:
    """Called on the worker when the TCP connection to the manager drops."""
    log.warning("[MAIN] Manager disconnected.")
    slot = _call_slots.get("manager")
    if slot and slot.is_in_call:
        slot.on_call_ended()
    speak(t("manager_disconnected"), config.HELMET_LANGUAGE_CODE)

def _on_worker_connected(worker_id: str) -> None:
    try:
        n = config._FIXED_WORKER_ORDER.index(worker_id) + 1
    except ValueError:
        n = "?"
    log.info("[MAIN] Worker '%s' connected (slot %s).", worker_id, n)
    speak(t("worker_connected", n=n), config.HELMET_LANGUAGE_CODE)

    if config.HELMET_ROLE != "manager":
        network.set_manager_event_callbacks(on_disconnect=_on_manager_disconnected)
        network.connect_to_server(config.MANAGER_IP, config.COMM_PORT, _on_network_message)


def _on_worker_disconnected(worker_id: str) -> None:
    try:
        n = config._FIXED_WORKER_ORDER.index(worker_id) + 1
    except ValueError:
        n = "?"
    log.info("[MAIN] Worker '%s' disconnected.", worker_id)
    slot = _get_slot_for_worker(worker_id)
    if slot and slot.is_in_call:
        slot.on_call_ended()
    speak(t("worker_disconnected", n=n), config.HELMET_LANGUAGE_CODE)


# ─────────────────────────────────────────────────────────────────────────────
# Button handlers – PTT (send side)
# ─────────────────────────────────────────────────────────────────────────────

def _is_in_any_call() -> bool:
    return any(s.is_in_call for s in _call_slots.values())


def _get_active_call_slot() -> Optional[CallSlot]:
    for s in _call_slots.values():
        if s.is_in_call:
            return s
    return None


def _record_and_send(channel: str) -> None:
    pin = (config.BTN_SPEAK_MANAGER if channel == "manager"
           else config.BTN_SPEAK_WORKER)

    speak(t("recording"), config.HELMET_LANGUAGE_CODE)

    time.sleep(0.5)
    
    is_held = lambda: gpio.is_pressed(pin)
    audio = record_until_release(is_held)

    if len(audio) < config.CHUNK * 4:
        speak(t("too_short_hold"), config.HELMET_LANGUAGE_CODE)
        return

    text, detected_lang = transcribe_only(audio)

    if not text:
        speak(t("not_understood_retry"), config.HELMET_LANGUAGE_CODE)
        return

    lang = detected_lang if detected_lang in ("en", "de") else config.HELMET_LANGUAGE
    log.info("[MAIN] PTT [%s] '%s' lang=%s", channel, text, lang)

    if channel == "manager":
        if config.HELMET_ROLE == "manager":
            with _selection_lock:
                target = _selected_worker_id
            if target is None:
                speak(t("no_worker_selected"), config.HELMET_LANGUAGE_CODE)
                return
            ok = network.send_voice_message_to(target, text, lang)
        else:
            ok = network.send_voice_message(text, lang)
    else:
        ok = peer_network.send_voice_message_to_peer(
            _get_peer_worker_ip(), config.PEER_PORT,
            text, lang, sender_id=config.HELMET_ID,
        )

    speak(t("message_sent") if ok else t("partner_unreachable"),
          config.HELMET_LANGUAGE_CODE)


def _handle_speak_manager() -> None:
    active_slot = _get_active_call_slot()
    if active_slot:
        speak(t("muted"), config.HELMET_LANGUAGE_CODE)
        active_slot.mute(True)
        while gpio.is_pressed(config.BTN_SPEAK_MANAGER):
            time.sleep(0.05)
        active_slot.mute(False)
        speak(t("unmuted"), config.HELMET_LANGUAGE_CODE)
        return
    _record_and_send("manager")


def _handle_speak_worker() -> None:
    """W key / GPIO 24 — PTT to peer worker (workers only)."""
    if config.HELMET_ROLE == "manager":
        return
    _record_and_send("peer")


# ─────────────────────────────────────────────────────────────────────────────
# Button handlers – CALL  (per slot)
# ─────────────────────────────────────────────────────────────────────────────

def _handle_call_button(slot_id: str) -> None:
    slot = _call_slots.get(slot_id)
    if slot is None:
        log.warning("[MAIN] No call slot for '%s'.", slot_id)
        return

    for sid, s in _call_slots.items():
        if sid != slot_id and s.is_in_call:
            speak(t("already_in_call"), config.HELMET_LANGUAGE_CODE)
            return

    slot.on_button_pressed()


def _play_next_message() -> None:
    msg = message_store.peek()
    if msg is None:
        lang_name = "English" if config.HELMET_LANGUAGE == "en" else "Deutsch"
        speak(t("no_messages_lang", lang_name=lang_name), config.HELMET_LANGUAGE_CODE)
        return

    sender_label = msg["sender_role"].capitalize()
    preview = _preview_text(msg["text"], max_words=6)
    speak(t("from_sender", sender=sender_label, preview=preview), config.HELMET_LANGUAGE_CODE)

    my_lang  = config.HELMET_LANGUAGE
    src_lang = msg["language"]
    text     = msg["text"]

    if src_lang == my_lang:
        wav = text_to_speech(text, _lang_code(my_lang))
    else:
        translated = translate_text(text, src_lang, my_lang)
        wav = text_to_speech(translated, _lang_code(my_lang))

    play_audio_bytes(wav)
    message_store.set_last_played(msg)
    message_store.pop()

    remaining = message_store.count()
    if remaining > 0:
        speak(t("n_messages_remaining", n=remaining), config.HELMET_LANGUAGE_CODE)
    else:
        led_handler.stop_pending_blink()
        speak(t("no_more_messages"), config.HELMET_LANGUAGE_CODE)


def _replay_last_message() -> None:
    msg = message_store.get_last_played()
    if msg is None:
        speak(t("no_message_to_replay"), config.HELMET_LANGUAGE_CODE)
        return

    sender_label = msg["sender_role"].capitalize()
    preview = _preview_text(msg["text"], max_words=6)
    speak(t("replaying_from_sender", sender=sender_label, preview=preview),
          config.HELMET_LANGUAGE_CODE)

    my_lang  = config.HELMET_LANGUAGE
    src_lang = msg["language"]
    text     = msg["text"]

    if src_lang == my_lang:
        wav = text_to_speech(text, _lang_code(my_lang))
    else:
        translated = translate_text(text, src_lang, my_lang)
        wav = text_to_speech(translated, _lang_code(my_lang))

    play_audio_bytes(wav)

def _play_or_replay_message() -> None:
    """
    Single tap: play the next unread message.
    If there are no unread messages, fall back to replaying the last one heard.
    """
    msg = message_store.peek()
    if msg is not None:
        _play_next_message()
        return

    last = message_store.get_last_played()
    if last is not None:
        _replay_last_message()
        return

    lang_name = "English" if config.HELMET_LANGUAGE == "en" else "Deutsch"
    speak(t("no_messages_lang", lang_name=lang_name), config.HELMET_LANGUAGE_CODE)

# ─────────────────────────────────────────────────────────────────────────────
# Button handler – PLAY message
# ─────────────────────────────────────────────────────────────────────────────

TAP_WINDOW_SECS = 0.6   # max gap between taps to count as the same sequence

_play_tap_count = 0
_play_tap_timer: threading.Timer | None = None
_play_tap_lock = threading.Lock()


def _dispatch_play_taps() -> None:
    """Called once the tap window expires — acts on however many taps were counted."""
    global _play_tap_count
    with _play_tap_lock:
        count = _play_tap_count
        _play_tap_count = 0

    if count == 1:
        _play_or_replay_message()
    else:
        _handle_language_config()


def _handle_play_msg() -> None:
    """
    PLAY button — short press only, no hold logic.
    Counts taps within TAP_WINDOW_SECS, then dispatches once the window closes:
      1 tap  → play next message, or replay last if none
      2 taps → voice language configuration
      long press → emergency broadcast to all
    """
    global _play_tap_count, _play_tap_timer

    if _is_in_any_call():
        return   # don't trigger any of this during an active call

    start_time = time.time()
    
    # 1. HOLD LOGIC: Check if they hold it for 3 seconds FIRST
    while gpio.is_pressed(config.BTN_PLAY_MSG):
        if time.time() - start_time >= 3.0:
            log.info("[MAIN] 3-Second Hold Detected: Triggering Emergency.")
            _trigger_emergency()
            # Wait for them to let go so we don't accidentally count it as a tap later
            while gpio.is_pressed(config.BTN_PLAY_MSG):
                time.sleep(0.05)
            return
        time.sleep(0.05)

    with _play_tap_lock:
        _play_tap_count += 1

        if _play_tap_timer is not None:
            _play_tap_timer.cancel()

        _play_tap_timer = threading.Timer(TAP_WINDOW_SECS, _dispatch_play_taps)
        _play_tap_timer.daemon = True
        _play_tap_timer.start()


# ─────────────────────────────────────────────────────────────────────────────
# Button handler – Language configuration
# ─────────────────────────────────────────────────────────────────────────────

def _handle_language_config() -> None:
    speak(t("lang_setup_prompt"), config.HELMET_LANGUAGE_CODE)
    time.sleep(0.5)

    start_time = time.time()
    hands_free_timer = lambda: (time.time() - start_time) < 2.0
    
    audio = record_until_release(hands_free_timer, max_seconds=2.0)

    if not audio or len(audio) < config.CHUNK * 2.0:
        speak(t("no_input_cancelled"), config.HELMET_LANGUAGE_CODE)
        return

    lang = ""
    for hint in ("en", "de"):
        text = voice_to_text(audio, language_code=hint)
        log.info("[LANG CFG] Heard (hint=%s): '%s'", hint, text)
        lang = parse_config_command(text)
        if lang:
            break

    if not lang:
        speak(t("lang_not_understood"), config.HELMET_LANGUAGE_CODE)
        return

    save_language_setting(lang)
    apply_language_setting(lang)
    speak(t("configured_en") if lang == "en" else t("configured_de"),
          config.HELMET_LANGUAGE_CODE)


def _trigger_emergency() -> None:
    log.warning("[EMERGENCY] Triggered by %s.", config.HELMET_ID)
    play_alert_beep(5)
    led_handler.blink_alert(times=20, interval=0.08)
    speak(t("emergency_prompt"), config.HELMET_LANGUAGE_CODE)
    time.sleep(0.5)

    is_held = lambda: gpio.is_pressed(config.BTN_PLAY_MSG)
    audio = record_until_release(is_held)

    custom_message = ""
    if audio and len(audio) >= config.CHUNK * 2:
        text, _ = transcribe_only(audio)
        if text:
            custom_message = text

    if not custom_message:
        custom_message = t("emergency_message")
        log.warning("[EMERGENCY] No voice detected. Using default message.")
        speak(t("not_understood_retry"), config.HELMET_LANGUAGE_CODE)
    else:
        log.info("[EMERGENCY] Custom message recorded: '%s'", custom_message)
    
    if config.HELMET_ROLE == "manager":
        # The Manager blasts the message to every connected worker simultaneously.
        sent = network.send_voice_message_to_all(custom_message, config.HELMET_LANGUAGE, is_emergency=True)
        log.warning("[EMERGENCY] Broadcast sent to all %d connected worker(s).", sent)
    else:
        network.send_voice_message(custom_message, config.HELMET_LANGUAGE, is_emergency=True)
        
        peers_notified = 0
        for wid, ip in config.WORKER_IPS.items():
            if wid != config.HELMET_ID:  # Do not send the emergency to yourself
                peer_network.send_voice_message_to_peer(
                    ip, config.PEER_PORT,
                    custom_message, config.HELMET_LANGUAGE, sender_id=config.HELMET_ID,
                    is_emergency=True,
                )
                peers_notified += 1
                
        log.warning("[EMERGENCY] Broadcast sent to Manager and %d Peer Worker(s).", peers_notified)


def _handle_emergency_incoming(meta: dict) -> None:
    """Play emergency alert when received from another helmet."""
    sender_id   = meta.get("sender_id", "unknown")
    sender_role = meta.get("sender_role", "unknown")
    message     = meta.get("text", "")
    src_lang    = meta.get("language", config.HELMET_LANGUAGE)

    label = sender_id if sender_role == "worker" else "manager"
    log.warning("[MAIN] EMERGENCY received from %s.", sender_id)
    
    play_alert_beep(5)
    led_handler.blink_alert(times=20, interval=0.08)
    speak(t("emergency_from", sender=label), config.HELMET_LANGUAGE_CODE)

    if message:
        my_lang = config.HELMET_LANGUAGE
        
        # Translate if the sender speaks a different language
        if src_lang != my_lang:
            translated_text = translate_text(message, src_lang, my_lang)
        else:
            translated_text = message
            
        audio = text_to_speech(translated_text, _lang_code(my_lang))
        play_audio_bytes(audio)

# ─────────────────────────────────────────────────────────────────────────────
# Other button handlers
# ─────────────────────────────────────────────────────────────────────────────
_play_tap_count = 0
_play_tap_timer: threading.Timer | None = None
_play_tap_lock = threading.Lock()

def _dispatch_reminder_taps() -> None:
    """Called once the tap window expires for the Reminder button."""
    global _play_tap_count
    with _play_tap_lock:
        count = _play_tap_count
        _play_tap_count = 0

    # Whether they tap it once, or double-tap it, just replay the last reminder!
    if count >= 1:
        log.info("[MAIN] Tap detected: Replay Reminder.")
        replay_last_reminder()


def _handle_reminder() -> None:

    global _play_tap_count, _play_tap_timer

    if _is_in_any_call():
        return   # don't play stored messages over live call audio

    start_time = time.time()

    # 1. HOLD LOGIC: Check if they hold it for 3 seconds FIRST
    while gpio.is_pressed(config.BTN_REMINDER):
        if time.time() - start_time >= 1.5:
            log.info("[MAIN] 1.5-Second Hold Detected: Recording Reminder.")
            # Pass the lambda so it keeps recording until they physically let go
            is_held = lambda: gpio.is_pressed(config.BTN_REMINDER)
            record_and_save_reminder(is_held)
            
            # Wait for them to let go so we don't accidentally count it as a tap later
            while gpio.is_pressed(config.BTN_REMINDER):
                time.sleep(0.05)
            return
        time.sleep(0.05)

    # 2. TAP LOGIC: If they let go before 1.5 seconds, it's a short tap.
    with _play_tap_lock:
        _play_tap_count += 1

        if _play_tap_timer is not None:
            _play_tap_timer.cancel()

        # Uses your standard TAP_WINDOW_SECS (e.g., 0.6 seconds)
        _play_tap_timer = threading.Timer(TAP_WINDOW_SECS, _dispatch_reminder_taps)
        _play_tap_timer.daemon = True
        _play_tap_timer.start()


def _handle_handover() -> None:
  
    """
    HANDOVER button:
    - 3s Hold: Record new handover
    - 1 Tap: Play unplayed handover
    - 2 Taps: Replay last recorded handover
    """
    global _play_tap_count, _play_tap_timer

    if _is_in_any_call():
        return   # don't trigger any of this during an active call

    start_time = time.time()

    # 1. HOLD LOGIC: Check if they hold it for 1.5 seconds FIRST
    while gpio.is_pressed(config.BTN_HANDOVER):
        if time.time() - start_time >= 1.5:
            log.info("[MAIN] 1.5-Second Hold Detected: Recording Handover.")
            # Pass the lambda so it keeps recording until they physically let go
            is_held = lambda: gpio.is_pressed(config.BTN_HANDOVER)
            record_handover(is_held)
            
            # Wait for them to let go so we don't accidentally count it as a tap
            while gpio.is_pressed(config.BTN_HANDOVER):
                time.sleep(0.05)
            return
        time.sleep(0.05)

    # 2. TAP LOGIC: If they let go before 3 seconds, it's a short tap.
    with _play_tap_lock:
        _play_tap_count += 1

        if _play_tap_timer is not None:
           _play_tap_timer.cancel()

        _play_tap_timer = threading.Timer(TAP_WINDOW_SECS, _dispatch_handover_taps)
        _play_tap_timer.daemon = True
        _play_tap_timer.start()


def _dispatch_handover_taps() -> None:
    """Called once the tap window expires for the Handover button."""
    global _play_tap_count
    with _play_tap_lock:
        count = _play_tap_count
        _play_tap_count = 0

    # Whether they tap it once, or accidentally double-tap it, just play the note!
    if count >= 1:
        log.info("[MAIN] Tap detected: Play Handover.")
        handle_handover_button()


# ─────────────────────────────────────────────────────────────────────────────
# Manager F1/F2 and number-key listeners  (keyboard hooks)
# ─────────────────────────────────────────────────────────────────────────────

def _start_manager_keyboard_hooks() -> None:
    """Register F1/F2 call buttons and 1/2 PTT selection for manager."""
    if not gpio.HAS_KEYBOARD:
        log.warning("[MAIN] keyboard lib unavailable – F1/F2 and 1/2 disabled.")
        return
    import keyboard as _kb

    _f_workers = {
        "f1": "w-01",
        "f2": "w-02",
    }

    def _on_key(event):
        if event.event_type != "down":
            return
        k = event.name.lower()
        if k in _f_workers:
            wid = _f_workers[k]
            threading.Thread(target=_handle_call_button, args=(wid,),
                             daemon=True, name=f"call-{wid}").start()
        elif k in ("1", "2"):
            _select_worker(int(k))

    _kb.hook(_on_key)
    log.info("[MAIN] Manager keyboard: F1=call-w-01  F2=call-w-02  "
             "1/2=PTT-select")


# ─────────────────────────────────────────────────────────────────────────────
# Button monitor threads
# ─────────────────────────────────────────────────────────────────────────────

def _button_monitor(pin: int, handler, name: str) -> None:
    log.info("[MONITOR] %s on pin %d.", name, pin)
    while True:
        gpio.wait_for_press(pin)
        try:
            handler()
        except Exception as exc:
            log.error("[MONITOR] Error in %s: %s", name, exc)
        time.sleep(0.25)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Language
    lang = load_language_setting()
    apply_language_setting(lang)



    log.info("=" * 60)
    log.info("  Smart Helmet – Communication Module")
    log.info("  Role     : %s  (%s)", config.HELMET_ROLE, config.HELMET_ID)
    log.info("  Language : %s", config.HELMET_LANGUAGE_CODE)
    log.info("  Manager  : %s:%d", config.MANAGER_IP, config.COMM_PORT)
    if config.HELMET_ROLE == "worker":
        log.info("  Peer     : %s:%d", _get_peer_worker_ip(), config.PEER_PORT)
    log.info("=" * 60)

    # 2. Offline models
    log.info("[MAIN] Loading offline models…")
    setup_offline_models()

    # 3. Database
    db.check_and_recover_db() 
    db.init_db()
    db.cleanup_old_records(days=7)

    # 4. GPIO / keyboard + LED
    gpio.setup_gpio()
    led_handler.setup(config.LED_MSG_PIN)

    # 5. Reminder loop
    start_reminder_loop()

    # 6. Build call slots
    #
    # LiveCall factory captures port_offset per worker so ports don't collide.
    #
    if config.HELMET_ROLE == "manager":
        for wid in ("w-01", "w-02"):
            offset = config.CALL_PORT_OFFSETS.get(wid, 0)
            label  = "w-01" if wid == "w-01" else "w-02"

            def _factory(partner_ip, _offset=offset):
                return _make_live_call(partner_ip, _offset)

            # worker_ip is looked up in network.get_worker_ip() at call time
            # We pass a placeholder; CallSlot.on_call_request_received fills
            # the real IP via network helpers.
            _call_slots[wid] = CallSlot(
                slot_id=wid,
                partner_label=label,
                partner_ip=config.WORKER_IPS.get(wid, ""),           # resolved at call time (see note below)
                send_request_fn=lambda sid: network.send_call_request(sid),
                send_accept_fn=lambda sid: network.send_call_accept(sid),
                send_end_fn=lambda sid: network.send_call_end(sid),
                live_call_factory=_factory,
            )
            log.info("[MAIN] Call slot created for %s (%s).", wid, label)

    else:
        # Worker: one slot to manager — use THIS worker's own offset, not hardcoded 0
        my_offset = config.CALL_PORT_OFFSETS.get(config.HELMET_ID, 0)

        _call_slots["manager"] = CallSlot(
            slot_id="manager",
            partner_label="manager",
            partner_ip=config.MANAGER_IP,
            send_request_fn=lambda sid: network.send_call_request(sid),
            send_accept_fn=lambda sid: network.send_call_accept(sid),
            send_end_fn=lambda sid: network.send_call_end(sid),
            live_call_factory=lambda ip, _offset=my_offset: _make_live_call(ip, _offset),
        )
        log.info("[MAIN] Call slot created for manager (offset=%d).", my_offset)

    # 7. Network
    if config.HELMET_ROLE == "manager":
        network.set_worker_event_callbacks(
            on_connect=_on_worker_connected,
            on_disconnect=_on_worker_disconnected,
        )
        network.start_server(config.COMM_PORT, _on_network_message)
        log.info("[MAIN] Manager TCP server on port %d.", config.COMM_PORT)
        _start_manager_keyboard_hooks()
    else:
        network.connect_to_server(config.MANAGER_IP, config.COMM_PORT,
                                  _on_network_message)
        log.info("[MAIN] Worker connecting to manager at %s:%d.",
                 config.MANAGER_IP, config.COMM_PORT)
        peer_network.start_peer_server(config.PEER_PORT, _on_peer_message)
        log.info("[MAIN] Peer server on port %d.", config.PEER_PORT)

    # 8. Button monitor threads
    monitors = [
        threading.Thread(
            target=_button_monitor,
            args=(config.BTN_SPEAK_MANAGER, _handle_speak_manager, "SPEAK_MGR"),
            name="btn-speak-mgr", daemon=True),
        threading.Thread(
            target=_button_monitor,
            args=(config.BTN_SPEAK_WORKER, _handle_speak_worker, "SPEAK_WKR"),
            name="btn-speak-wkr", daemon=True),
        threading.Thread(
            target=_button_monitor,
            args=(config.BTN_PLAY_MSG, _handle_play_msg, "PLAY_MSG"),
            name="btn-play", daemon=True),
        threading.Thread(
            target=_button_monitor,
            args=(config.BTN_REMINDER, _handle_reminder, "REMINDER"),
            name="btn-reminder", daemon=True),
        threading.Thread(
            target=_button_monitor,
            args=(config.BTN_HANDOVER, _handle_handover, "HANDOVER"),
            name="btn-handover", daemon=True),
    ]

    # Worker call button (C key / GPIO 23)
    if config.HELMET_ROLE == "worker":
        monitors.append(threading.Thread(
            target=_button_monitor,
            args=(config.BTN_CALL_MANAGER,
                  lambda: _handle_call_button("manager"), "CALL_MGR"),
            name="btn-call-mgr", daemon=True))

    for monitor_thread in monitors:
        monitor_thread.start()

    # 9. Ready
    log.info("[MAIN] System ready.")
    speak(t("smart_helmet_ready"), config.HELMET_LANGUAGE_CODE)
    if config.HELMET_ROLE == "manager" and not gpio.IS_PI:
        log.info("[MAIN] Keys: SPACE=PTT  1/2=select  F1/F2=call  "
                 "p=play  r=reminder  h=handover")
    elif config.HELMET_ROLE == "worker" and not gpio.IS_PI:
        log.info("[MAIN] Keys: SPACE=PTT-mgr  w=PTT-peer  c=call-mgr  "
                 "p=play  r=reminder  h=handover")

    # 10. Keep alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("[MAIN] Shutdown.")
    finally:
        message_store.clear()
        gpio.cleanup()
        log.info("[MAIN] Goodbye.")


if __name__ == "__main__":
    main()
