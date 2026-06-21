# handover.py – Shift / task handover message management.
#
# Single-button logic (BTN_HANDOVER):
#   • If an UNPLAYED handover message exists for this helmet's role → PLAY it.
#   • If no unplayed message exists → RECORD a new handover (hold-to-talk).
#
# Recording captures: name + zone/location + message (all spoken together).
# The full transcribed text is saved; zone and name are extracted via simple
# heuristics or left as "spoken" for the prototype.
#
# Database schema (created by db.init_db()):
#   handover(id, sender_name, sender_role, zone, message, timestamp, played)

import logging
from datetime import datetime

import config
import db
from translation import (
    record_until_release,
    voice_to_text,
    text_to_speech,
    play_audio_bytes,
    speak,
)

log = logging.getLogger(__name__)


# ─── Check for unplayed message ───────────────────────────────────────────────

def _get_unplayed_handover() -> dict | None:
    """
    Return the oldest handover message from the OTHER role that THIS specific
    helmet (by HELMET_ID) has not yet played.
    """
    other_role = "manager" if config.HELMET_ROLE == "worker" else "worker"
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT id, sender_name, sender_id, sender_role, zone, message,
                  timestamp, played_by
           FROM handover
           WHERE sender_role = ?
           ORDER BY id ASC""",
        (other_role,),
    ).fetchall()
    conn.close()

    my_id = config.HELMET_ID
    for row in rows:
        row_dict = dict(row)
        already_played = (row_dict.get("played_by") or "").split(",")
        if my_id not in already_played:
            return row_dict
    return None


def _mark_played(handover_id: int) -> None:
    """Record that THIS helmet has now played this message (others still can)."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT played_by FROM handover WHERE id = ?", (handover_id,)
    ).fetchone()
    existing = (row["played_by"] or "") if row else ""
    ids = [x for x in existing.split(",") if x]
    if config.HELMET_ID not in ids:
        ids.append(config.HELMET_ID)
    conn.execute(
        "UPDATE handover SET played_by = ? WHERE id = ?",
        (",".join(ids), handover_id),
    )
    conn.commit()
    conn.close()


# ─── Playback ────────────────────────────────────────────────────────────────

def play_handover(entry: dict) -> None:
    """Synthesise and play a handover entry via TTS in the user's language."""
    zone_part = f"Zone {entry['zone']}. " if entry.get("zone") else ""
    name_part = entry.get("sender_name") or entry["sender_role"].capitalize()
    full_text = (
        f"Handover from {name_part} at {entry['timestamp']}. "
        f"{zone_part}"
        f"{entry['message']}"
    )
    log.info("[HANDOVER] Playing: %s", full_text)
    speak("Playing handover message.", config.HELMET_LANGUAGE_CODE)
    audio = text_to_speech(full_text, language_code=config.HELMET_LANGUAGE_CODE)
    play_audio_bytes(audio)
    _mark_played(entry["id"])
    speak("End of handover message.", config.HELMET_LANGUAGE_CODE)


# ─── Recording ───────────────────────────────────────────────────────────────

def record_handover(is_held_fn) -> None:
    """
    Record a new handover message (hold-to-talk), save to DB.
    User should speak: name, zone/location, then the message.
    Example: "This is Ahmed, Zone B. The scaffolding on level 3 needs checking."
    """
    speak(
        "Hold the button and record your handover. "
        "Say your name, zone, and your message.",
        config.HELMET_LANGUAGE_CODE,
    )

    audio = record_until_release(is_held_fn, max_seconds=config.RECORD_SECONDS_MAX)

    if len(audio) < config.CHUNK * 2:
        speak("Recording too short. Handover not saved.", config.HELMET_LANGUAGE_CODE)
        return

    speak("Processing handover.", config.HELMET_LANGUAGE_CODE)
    text = voice_to_text(audio, language_code=config.HELMET_LANGUAGE_CODE)

    if not text:
        speak("Could not understand. Handover not saved.", config.HELMET_LANGUAGE_CODE)
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = db.get_conn()
    conn.execute(
        """INSERT INTO handover
            (sender_name, sender_id, sender_role, zone, message, timestamp, played_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            None,                    # name – embedded in spoken message for prototype
            config.HELMET_ID,        # ← now actually recorded: "w-01", "w-02", or "manager"
            config.HELMET_ROLE,
            None,                    # zone – embedded in spoken message for prototype
            text,
            timestamp,
            "",                      # nobody has played it yet
        ),
    )
    conn.commit()
    conn.close()

    log.info("[HANDOVER] Saved: '%s'", text)
    speak("Handover message saved.", config.HELMET_LANGUAGE_CODE)


# ─── Button handler (called from main event loop) ─────────────────────────────

def handle_handover_button(is_held_fn) -> None:
    """
    Single entry point for BTN_HANDOVER press.
    Decision: play unread message → OR → record new message.
    """
    entry = _get_unplayed_handover()
    if entry:
        play_handover(entry)
    else:
        record_handover(is_held_fn)