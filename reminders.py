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
import dateparser
from dateparser.search import search_dates as _dp_search
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


# ─── Save reminder ────────────────────────────────────────────────────────────

def save_reminder(message: str, trigger_dt: datetime) -> None:
    """Persist a reminder with a full datetime trigger."""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    trigger_str = trigger_dt.strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO reminders (message, trigger_datetime, done, created_at) VALUES (?, ?, 0, ?)",
        (message, trigger_str, created_at),
    )
    conn.commit()
    conn.close()
    log.info("[REMINDER] Saved: '%s' at %s", message, trigger_str)


# ─── Record + save flow (triggered by button) ─────────────────────────────────

def record_and_save_reminder(is_held_fn) -> None:
    speak(t("reminder_prompt"), config.HELMET_LANGUAGE_CODE)

    time.sleep(0.5)
    
    audio = record_until_release(is_held_fn, max_seconds=config.RECORD_SECONDS_MAX)

    if len(audio) < config.CHUNK * 2:
        speak(t("reminder_too_short"), config.HELMET_LANGUAGE_CODE)
        return

    text = voice_to_text(audio, language_code=config.HELMET_LANGUAGE_CODE)

    if not text:
        speak(t("not_understood_retry"), config.HELMET_LANGUAGE_CODE)
        return

    trigger_dt = parse_trigger_datetime(text)
    if not trigger_dt:
        speak(t("reminder_no_time"), config.HELMET_LANGUAGE_CODE)
        return

    now = datetime.now()
    if trigger_dt.date() == now.date():
        friendly = f"today at {trigger_dt.strftime('%H:%M')}"
    elif trigger_dt.date() == (now + timedelta(days=1)).date():
        friendly = f"tomorrow at {trigger_dt.strftime('%H:%M')}"
    else:
        friendly = trigger_dt.strftime("%A at %H:%M")   # e.g. "Saturday at 14:30"
    
    save_reminder(text, trigger_dt)
    speak(t("reminder_saved", time=friendly), config.HELMET_LANGUAGE_CODE)


def _check_and_play_reminders() -> None:
    global _last_triggered
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, message FROM reminders WHERE trigger_datetime <= ? AND done = 0",
        (now_str,),
    ).fetchall()
    for row in rows:
        log.info("[REMINDER] Triggered: '%s'", row["message"])
        play_alert_beep(2)
        speak(t("reminder_label"), config.HELMET_LANGUAGE_CODE)
        audio = text_to_speech(row["message"], language_code=config.HELMET_LANGUAGE_CODE)
        play_audio_bytes(audio)
        with _last_triggered_lock:                          # ← this line was missing
            _last_triggered = {"message": row["message"]}  # ← this line was missing
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
    

def _parse_digit_run_time(text: str, now: datetime) -> datetime | None:
    """
    Handle times Whisper transcribes as a bare digit run with no separator,
    e.g. "832" -> 8:32, "1430" -> 14:30, "9" -> hour only (assume :00).
    Only matches when the digits appear near a time-indicating word
    ("at", "um") to avoid false positives on unrelated numbers.
    """
    t = text.lower()
    m = re.search(r'\b(?:at|um)\s+(\d{1,4})\b', t)
    if not m:
        return None

    digits = m.group(1)

    if len(digits) <= 2:
        hour, minute = int(digits), 0
    elif len(digits) == 3:
        hour, minute = int(digits[0]), int(digits[1:])
    elif len(digits) == 4:
        hour, minute = int(digits[:2]), int(digits[2:])
    else:
        return None

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate < now:
        candidate += timedelta(days=1)
    return candidate


def parse_trigger_datetime(text: str, now: datetime = None) -> datetime | None:
    
    if now is None:
        now = datetime.now()

    # "the same time [tomorrow]" needs special handling — dateparser doesn't
    # know what "the same time" refers to without context
    t = text.lower()

    t = re.sub(r'(\d)\.(\d)', r'\1:\2', t)

    # Catch STT squished times like "at 431" or "um 1430"
    m = re.search(r'\b(?:at|um)\s+([012]?\d)([0-5]\d)\b', t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        parsed = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if parsed < now:
            parsed += timedelta(days=1)
        return parsed
    
    if "same time" in t or "gleiche zeit" in t:
        if "tomorrow" in t or "morgen" in t:
            return now.replace(second=0, microsecond=0) + timedelta(days=1)
        return now.replace(second=0, microsecond=0)
    
    # Handle Whisper's bare-digit time transcriptions ("at 832" -> 8:32)
    digit_run = _parse_digit_run_time(t, now)
    if digit_run:
        return digit_run

    # "after N(.N) hours" / "in N(.N) hours" — dateparser handles "in" well,
    # but decimal hours ("3.5 hours") sometimes need help
    m = re.search(r'(?:after|in)\s+(\d+(?:\.\d+)?)\s*hours?', t)
    if m:
        hours = float(m.group(1))
        return now + timedelta(hours=hours)

    m = re.search(r'(?:after|in)\s+(\d+(?:\.\d+)?)\s*min(?:ute)?s?', t)
    if m:
        minutes = float(m.group(1))
        return now + timedelta(minutes=minutes)

    settings = {"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now, "RETURN_AS_TIMEZONE_AWARE": False}
    
    results = _dp_search(text, languages=["en", "de"], settings=settings)
    if results:
        _, parsed = results[0]   # take the first match found
        if parsed and parsed < now:
            parsed += timedelta(days=1)
        return parsed
    # Final fallback — try parsing the whole string directly
    parsed = dateparser.parse(text, languages=["en", "de"], settings=settings)
    if parsed and parsed < now:
        parsed += timedelta(days=1)
    return parsed