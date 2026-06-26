# handover.py – Shift / task handover message management.

import logging
from datetime import datetime

import config
import db
from translation import (
    record_until_release,
    transcribe_only,
    translate_text,
    text_to_speech,
    play_audio_bytes,
    speak,
    t,
)

log = logging.getLogger(__name__)


def _get_unplayed_handover() -> dict | None:
    other_role = "manager" if config.HELMET_ROLE == "worker" else "worker"
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT id, sender_name, sender_id, sender_role, zone, message,
                  timestamp, played_by, language
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


def play_handover(entry: dict) -> None:
    """
    Synthesise and play a handover entry via TTS in the listener's language.
    Translates the recorded message if the recorder's language differs from
    the listener's current HELMET_LANGUAGE.
    """
    zone_part = f"Zone {entry['zone']}. " if entry.get("zone") else ""
    name_part = entry.get("sender_name") or entry["sender_role"].capitalize()

    recorded_lang = entry.get("language") or "en"
    listener_lang = config.HELMET_LANGUAGE
    message = entry["message"]

    if recorded_lang != listener_lang:
        log.info("[HANDOVER] Translating %s -> %s for playback.", recorded_lang, listener_lang)
        message = translate_text(message, source=recorded_lang, target=listener_lang)

    full_text = (
        f"Handover from {name_part} at {entry['timestamp']}. "
        f"{zone_part}"
        f"{message}"
    )
    log.info("[HANDOVER] Playing: %s", full_text)
    speak(t("handover_playing"), config.HELMET_LANGUAGE_CODE)
    audio = text_to_speech(full_text, language_code=config.HELMET_LANGUAGE_CODE)
    play_audio_bytes(audio)
    _mark_played(entry["id"])
    speak(t("handover_end"), config.HELMET_LANGUAGE_CODE)


def record_handover(is_held_fn) -> None:
    """
    Record a new handover message (hold-to-talk), save to DB along with
    the recorder's current language, so playback can translate correctly.
    """
    speak(t("handover_prompt"), config.HELMET_LANGUAGE_CODE)

    audio = record_until_release(is_held_fn, max_seconds=config.RECORD_SECONDS_MAX)

    if len(audio) < config.CHUNK * 2:
        speak(t("handover_too_short"), config.HELMET_LANGUAGE_CODE)
        return

    speak(t("handover_processing"), config.HELMET_LANGUAGE_CODE)

    # Use the helmet's own configured language as the hint — more reliable
    # than blind auto-detect for short/accented speech (same lesson learned
    # from the language-config bug earlier).
    text, detected_lang = transcribe_only(audio)
    recorded_lang = detected_lang if detected_lang in ("en", "de") else config.HELMET_LANGUAGE

    if not text:
        speak(t("handover_not_understood"), config.HELMET_LANGUAGE_CODE)
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = db.get_conn()
    conn.execute(
        """INSERT INTO handover
            (sender_name, sender_id, sender_role, zone, message, timestamp, played_by, language)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (None, config.HELMET_ID, config.HELMET_ROLE, None, text, timestamp, "", recorded_lang),
    )
    conn.commit()
    conn.close()

    log.info("[HANDOVER] Saved (%s): '%s'", recorded_lang, text)
    speak(t("handover_saved"), config.HELMET_LANGUAGE_CODE)


def handle_handover_button(is_held_fn) -> None:
    entry = _get_unplayed_handover()
    if entry:
        play_handover(entry)
    else:
        record_handover(is_held_fn)


# ─── Replay (added earlier — kept consistent with translation fix) ──────────

def get_last_handover_for_replay() -> dict | None:
    other_role = "manager" if config.HELMET_ROLE == "worker" else "worker"
    conn = db.get_conn()
    row = conn.execute(
        """SELECT id, sender_name, sender_id, sender_role, zone, message,
                  timestamp, played_by, language
           FROM handover WHERE sender_role = ? ORDER BY id DESC LIMIT 1""",
        (other_role,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def replay_last_handover() -> None:
    entry = get_last_handover_for_replay()
    if entry is None:
        speak(t("no_handover_to_replay"), config.HELMET_LANGUAGE_CODE)
        return

    zone_part = f"Zone {entry['zone']}. " if entry.get("zone") else ""
    name_part = entry.get("sender_name") or entry["sender_role"].capitalize()

    recorded_lang = entry.get("language") or "en"
    listener_lang = config.HELMET_LANGUAGE
    message = entry["message"]
    if recorded_lang != listener_lang:
        message = translate_text(message, source=recorded_lang, target=listener_lang)

    full_text = (f"Handover from {name_part} at {entry['timestamp']}. "
                f"{zone_part}{message}")

    speak(t("handover_replaying"), config.HELMET_LANGUAGE_CODE)
    audio = text_to_speech(full_text, language_code=config.HELMET_LANGUAGE_CODE)
    play_audio_bytes(audio)
    speak(t("handover_end"), config.HELMET_LANGUAGE_CODE)