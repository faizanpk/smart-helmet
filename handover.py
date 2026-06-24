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
    speak, t,
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
    zone_part = f"Zone {entry['zone']}. " if entry.get("zone") else ""
    name_part = entry.get("sender_name") or entry["sender_role"].capitalize()
    full_text = (
        f"Handover from {name_part} at {entry['timestamp']}. "
        f"{zone_part}"
        f"{entry['message']}"
    )
    log.info("[HANDOVER] Playing: %s", full_text)
    speak(t("handover_playing"), config.HELMET_LANGUAGE_CODE)
    audio = text_to_speech(full_text, language_code=config.HELMET_LANGUAGE_CODE)
    play_audio_bytes(audio)
    _mark_played(entry["id"])
    speak(t("handover_end"), config.HELMET_LANGUAGE_CODE)


def record_handover(is_held_fn) -> None:
    speak(t("handover_prompt"), config.HELMET_LANGUAGE_CODE)

    audio = record_until_release(is_held_fn, max_seconds=config.RECORD_SECONDS_MAX)

    if len(audio) < config.CHUNK * 2:
        speak(t("handover_too_short"), config.HELMET_LANGUAGE_CODE)
        return

    speak(t("handover_processing"), config.HELMET_LANGUAGE_CODE)
    text = voice_to_text(audio, language_code=config.HELMET_LANGUAGE_CODE)

    if not text:
        speak(t("handover_not_understood"), config.HELMET_LANGUAGE_CODE)
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = db.get_conn()
    conn.execute(
        """INSERT INTO handover
            (sender_name, sender_id, sender_role, zone, message, timestamp, played_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (None, config.HELMET_ID, config.HELMET_ROLE, None, text, timestamp, ""),
    )
    conn.commit()
    conn.close()

    log.info("[HANDOVER] Saved: '%s'", text)
    speak(t("handover_saved"), config.HELMET_LANGUAGE_CODE)


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


def get_last_handover_for_replay() -> dict | None:
    """
    Return the most recent handover entry from the OTHER role, regardless
    of played_by state — used for manual replay, does not affect read status.
    """
    other_role = "manager" if config.HELMET_ROLE == "worker" else "worker"
    conn = db.get_conn()
    row = conn.execute(
        """SELECT id, sender_name, sender_id, sender_role, zone, message,
                  timestamp, played_by
           FROM handover WHERE sender_role = ? ORDER BY id DESC LIMIT 1""",
        (other_role,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def replay_last_handover() -> None:
    """Replay the most recent handover from the other role, without affecting read status."""
    entry = get_last_handover_for_replay()
    if entry is None:
        speak(t("no_handover_to_replay"), config.HELMET_LANGUAGE_CODE)
        return

    zone_part = f"Zone {entry['zone']}. " if entry.get("zone") else ""
    name_part = entry.get("sender_name") or entry["sender_role"].capitalize()
    full_text = (f"Handover from {name_part} at {entry['timestamp']}. "
                f"{zone_part}{entry['message']}")

    speak(t("handover_replaying"), config.HELMET_LANGUAGE_CODE)
    audio = text_to_speech(full_text, language_code=config.HELMET_LANGUAGE_CODE)
    play_audio_bytes(audio)
    speak(t("handover_end"), config.HELMET_LANGUAGE_CODE)