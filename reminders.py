# reminders.py – Task reminder / alarm management.
#
# Flow:
#   1. User holds BTN_REMINDER and speaks (e.g. "Check scaffolding at 14:30").
#   2. record_and_save_reminder() captures audio, runs STT, parses time from text.
#   3. The reminder is saved to SQLite with trigger_time = "14:30".
#   4. A background thread (start_reminder_loop) polls every 30 s;
#      at the matching HH:MM it plays the reminder text via TTS.
#
# Time parsing supports:
#   Explicit:  "14:30"  "9:00"  "at 2 pm"  "at 14 Uhr 30"
#   Words:     "noon"  "midnight"  "morning"  "afternoon"  "evening"
#              "Mittag"  "Morgen"  "Abend"

import re
import threading
import time
import logging
from datetime import datetime, timedelta

import schedule as _schedule  # third-party 'schedule' package

import config
import db
from translation import record_until_release, voice_to_text, text_to_speech, play_audio_bytes, speak, play_alert_beep, t

log = logging.getLogger(__name__)

_last_triggered: dict | None = None
_last_triggered_lock = threading.Lock()

# ─── Time parsing ─────────────────────────────────────────────────────────────

# Word → fixed time mapping (English and German)
_WORD_TIMES: dict[str, str] = {
    # English
    "midnight":  "00:00",
    "noon":      "12:00",
    "morning":   "09:00",
    "afternoon": "14:00",
    "evening":   "18:00",
    "night":     "20:00",
    # German
    "mitternacht": "00:00",
    "mittag":      "12:00",
    "morgen":      "09:00",  # "morgens" also covered
    "nachmittag":  "14:00",
    "abend":       "18:00",
    "nacht":       "20:00",
}


def _ampm_to_24(hour: int, period: str) -> str:
    if period.lower() == "pm" and hour < 12:
        hour += 12
    elif period.lower() == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:00"


def parse_trigger_time(text: str) -> str | None:
    """
    Extract a HH:MM (24-hour) trigger time from a transcribed reminder string.
    Returns a string like '14:30', or None if no time found.

    Explicit numeric times are checked FIRST since they are unambiguous.
    Word-based shortcuts ("noon", "Abend") are only used as a fallback,
    and only match whole words — not substrings inside longer words like
    "Abendessen".
    """
    t = text.lower()

    # 1. Explicit HH:MM  (e.g. "14:30", "9:00") — most precise, check first
    m = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', t)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    # 2. "at N am/pm"  (e.g. "at 2 pm", "at 10 am")
    m = re.search(r'\bat\s+(\d{1,2})\s*(am|pm)\b', t)
    if m:
        return _ampm_to_24(int(m.group(1)), m.group(2))

    # 3. German "N Uhr [M]"  (e.g. "14 Uhr 30", "um 9 Uhr")
    m = re.search(r'\b(\d{1,2})\s*uhr(?:\s+(\d{2}))?\b', t)
    if m:
        minutes = m.group(2) or "00"
        return f"{int(m.group(1)):02d}:{minutes}"

    # 4. Word-based shortcuts — fallback only, whole-word match required
    for word, hhmm in _WORD_TIMES.items():
        if re.search(rf'\b{re.escape(word)}\b', t):
            return hhmm
    
    # 5. Relative "in N minutes/hours" (English & German)
    m = re.search(r'\bin\s+(\d+)\s+(minute|minutes|minuten|hour|hours|stunde|stunden)\b', t)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        delta = timedelta(hours=val) if unit.startswith(('hour', 'stunde')) else timedelta(minutes=val)
        return (datetime.now() + delta).strftime("%H:%M")

    return None


# ─── Save reminder ────────────────────────────────────────────────────────────

def save_reminder(message: str, trigger_time: str) -> None:
    """Persist a reminder to the database."""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    with conn:
        conn.execute(
            "INSERT INTO reminders (message, trigger_time, done, created_at) VALUES (?, ?, 0, ?)",
            (message, trigger_time, created_at),
        )
    conn.close()
    log.info("[REMINDER] Saved: '%s' at %s", message, trigger_time)


# ─── Record + save flow (triggered by button) ─────────────────────────────────

def record_and_save_reminder(is_held_fn) -> None:
    speak(t("reminder_prompt"), config.HELMET_LANGUAGE_CODE)

    audio = record_until_release(is_held_fn, max_seconds=config.RECORD_SECONDS_MAX)

    if len(audio) < config.CHUNK * 2:
        speak(t("reminder_too_short"), config.HELMET_LANGUAGE_CODE)
        return

    speak(t("reminder_processing"), config.HELMET_LANGUAGE_CODE)
    text = voice_to_text(audio, language_code=config.HELMET_LANGUAGE_CODE)

    if not text:
        speak(t("not_understood_retry"), config.HELMET_LANGUAGE_CODE)
        return

    trigger_time = parse_trigger_time(text)
    if not trigger_time:
        speak(t("reminder_no_time"), config.HELMET_LANGUAGE_CODE)
        return

    save_reminder(text, trigger_time)
    speak(t("reminder_saved", time=trigger_time), config.HELMET_LANGUAGE_CODE)


def _check_and_play_reminders() -> None:
    global _last_triggered
    now = datetime.now().strftime("%H:%M")
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, message FROM reminders WHERE trigger_time = ? AND done = 0",
        (now,),
    ).fetchall()
    for row in rows:
        log.info("[REMINDER] Triggered: '%s'", row["message"])
        play_alert_beep(2)
        speak(t("reminder_label"), config.HELMET_LANGUAGE_CODE)
        audio = text_to_speech(row["message"], language_code=config.HELMET_LANGUAGE_CODE)
        play_audio_bytes(audio)
        with _last_triggered_lock:
            _last_triggered = {"message": row["message"]}
        conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()


def replay_last_reminder() -> None:
    """Replay the most recently fired reminder (single most recent only)."""
    with _last_triggered_lock:
        last = dict(_last_triggered) if _last_triggered else None
    if last is None:
        speak(t("no_reminder_to_replay"), config.HELMET_LANGUAGE_CODE)
        return
    speak(t("reminder_label"), config.HELMET_LANGUAGE_CODE)
    audio = text_to_speech(last["message"], language_code=config.HELMET_LANGUAGE_CODE)
    play_audio_bytes(audio)


# ─── Background reminder loop ─────────────────────────────────────────────────

def start_reminder_loop() -> None:
    """
    Start a background daemon thread that checks for due reminders every 30 s.
    Should be called once at startup.
    """
    _schedule.every(1).minutes.do(_check_and_play_reminders)

    def _run():
        while True:
            _schedule.run_pending()
            time.sleep(30)

    t = threading.Thread(target=_run, name="reminder-loop", daemon=True)
    t.start()
    log.info("[REMINDER] Background loop started (checks every 30 s).")