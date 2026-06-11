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
from datetime import datetime

import schedule as _schedule  # third-party 'schedule' package

import config
import db
from translation import record_until_release, voice_to_text, text_to_speech, play_audio_bytes, speak

log = logging.getLogger(__name__)

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
    """
    t = text.lower()

    # 1. Word-based shortcuts
    for word, hhmm in _WORD_TIMES.items():
        if word in t:
            return hhmm

    # 2. Explicit HH:MM  (e.g. "14:30", "9:00")
    m = re.search(r'\b(\d{1,2}):(\d{2})\b', t)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    # 3. "at N am/pm"  (e.g. "at 2 pm", "at 10 am")
    m = re.search(r'\bat\s+(\d{1,2})\s*(am|pm)\b', t)
    if m:
        return _ampm_to_24(int(m.group(1)), m.group(2))

    # 4. German "N Uhr [M]"  (e.g. "14 Uhr 30", "um 9 Uhr")
    m = re.search(r'\b(\d{1,2})\s*uhr(?:\s+(\d{2}))?\b', t)
    if m:
        minutes = m.group(2) or "00"
        return f"{int(m.group(1)):02d}:{minutes}"

    return None


# ─── Save reminder ────────────────────────────────────────────────────────────

def save_reminder(message: str, trigger_time: str) -> None:
    """Persist a reminder to the database."""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO reminders (message, trigger_time, done, created_at) VALUES (?, ?, 0, ?)",
        (message, trigger_time, created_at),
    )
    conn.commit()
    conn.close()
    log.info("[REMINDER] Saved: '%s' at %s", message, trigger_time)


# ─── Record + save flow (triggered by button) ─────────────────────────────────

def record_and_save_reminder(is_held_fn) -> None:
    """
    Full flow for the BTN_REMINDER button:
      1. Prompt user (TTS)
      2. Record while button held
      3. STT
      4. Parse time
      5. Save or report failure
    """
    speak("Hold the button and record your reminder.", config.HELMET_LANGUAGE_CODE)

    audio = record_until_release(is_held_fn, max_seconds=config.RECORD_SECONDS_MAX)

    if len(audio) < config.CHUNK * 2:
        speak("Recording too short. Please try again.", config.HELMET_LANGUAGE_CODE)
        return

    speak("Processing reminder.", config.HELMET_LANGUAGE_CODE)
    text = voice_to_text(audio, language_code=config.HELMET_LANGUAGE_CODE)

    if not text:
        speak("Could not understand. Please try again.", config.HELMET_LANGUAGE_CODE)
        return

    trigger_time = parse_trigger_time(text)
    if not trigger_time:
        speak(
            "No time found in your message. Please include a time, for example: at 14 30.",
            config.HELMET_LANGUAGE_CODE,
        )
        return

    save_reminder(text, trigger_time)
    speak(f"Reminder saved for {trigger_time}.", config.HELMET_LANGUAGE_CODE)


# ─── Playback check (runs every minute) ──────────────────────────────────────

def _check_and_play_reminders() -> None:
    """Check for due reminders and play them via TTS."""
    now = datetime.now().strftime("%H:%M")
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, message FROM reminders WHERE trigger_time = ? AND done = 0",
        (now,),
    ).fetchall()
    for row in rows:
        log.info("[REMINDER] Triggered: '%s'", row["message"])
        speak("Reminder:", config.HELMET_LANGUAGE_CODE)
        audio = text_to_speech(row["message"], language_code=config.HELMET_LANGUAGE_CODE)
        play_audio_bytes(audio)
        conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()


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