# handover.py – Local shift handover for this helmet/device.
#
# A user records a handover note for whoever uses this helmet next.
# The note is stored locally and played back when the next user presses the button.
#
# Button behaviour (H key / GPIO 22):
#   Short press  → if unplayed handover exists: PLAY IT
#                  if no unplayed handover: PLAY BACK own last recording (verify)
#   Long hold    → RECORD a new handover (replaces the previous one)
#   Double-tap   → REPLAY the last handover regardless of played state

import logging
import time
from datetime import datetime

import config
import db
from translation import (
    record_until_release,
    transcribe_only,
    text_to_speech,
    play_audio_bytes,
    speak,
    t,
)

log = logging.getLogger(__name__)


# ─── Database helpers ─────────────────────────────────────────────────────────

def _get_unplayed_handover() -> dict | None:
    """
    Return the most recent handover stored on THIS device that hasn't been
    played yet. No role filtering — it's local to this helmet.
    """
    conn = db.get_conn()
    row = conn.execute(
        """SELECT id, message, timestamp, language
           FROM handover
           WHERE played = 0
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _get_last_handover() -> dict | None:
    """Return the most recent handover on this device, played or not (for replay/verify)."""
    conn = db.get_conn()
    row = conn.execute(
        """SELECT id, message, timestamp, language
           FROM handover
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _mark_played(handover_id: int) -> None:
    conn = db.get_conn()
    conn.execute("UPDATE handover SET played = 1 WHERE id = ?", (handover_id,))
    conn.commit()
    conn.close()


# ─── Playback ─────────────────────────────────────────────────────────────────

def _play_entry(entry: dict, mark_as_played: bool = True) -> None:
    """Synthesise and play a handover entry via TTS."""
    speak(t("handover_playing"), config.HELMET_LANGUAGE_CODE)
    full_text = entry["message"]
    audio = text_to_speech(full_text, language_code=config.HELMET_LANGUAGE_CODE)
    play_audio_bytes(audio)
    if mark_as_played:
        _mark_played(entry["id"])


# ─── Recording ────────────────────────────────────────────────────────────────

def record_handover(is_held_fn) -> None:
    """
    Record a new handover. Deletes any previous handover on this device
    so only the latest note exists at any time.
    After saving, immediately reads it back so the user can verify.
    """
    speak(t("handover_prompt"), config.HELMET_LANGUAGE_CODE)

    time.sleep(0.5)
    
    audio = record_until_release(is_held_fn, max_seconds=config.RECORD_SECONDS_MAX)

    if len(audio) < config.CHUNK * 2:
        speak(t("handover_too_short"), config.HELMET_LANGUAGE_CODE)
        return

    text, detected_lang = transcribe_only(audio)
    recorded_lang = detected_lang if detected_lang in ("en", "de") else config.HELMET_LANGUAGE

    if not text:
        speak(t("handover_not_understood"), config.HELMET_LANGUAGE_CODE)
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = db.get_conn()
    # Delete all previous handovers on this device — only one active note at a time
    conn.execute("DELETE FROM handover")
    conn.execute(
        """INSERT INTO handover (message, timestamp, played, language, sender_role)
           VALUES (?, ?, 0, ?, ?)""",
        (text, timestamp, recorded_lang, config.HELMET_ROLE),
    )
    conn.commit()
    conn.close()

    log.info("[HANDOVER] Saved (%s): '%s'", recorded_lang, text)
    speak(t("handover_saved"), config.HELMET_LANGUAGE_CODE)

    audio_tts = text_to_speech(text, language_code=config.HELMET_LANGUAGE_CODE)
    play_audio_bytes(audio_tts)


# ─── Main button handler ──────────────────────────────────────────────────────

def handle_handover_button() -> None:
    """
    Short press:
      - Unplayed handover exists → play it (mark as played)
      - No unplayed → play last recorded (for re-verification, no state change)
    """
    entry = _get_unplayed_handover()
    if entry:
        _play_entry(entry, mark_as_played=True)
    else:
        last = _get_last_handover()
        if last:
            _play_entry(last, mark_as_played=False)
        else:
            speak(t("no_handover_to_replay"), config.HELMET_LANGUAGE_CODE)
