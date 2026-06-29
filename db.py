# db.py – Centralised SQLite database initialisation and helper functions.
# All modules import from here instead of duplicating DB logic.

import sqlite3
import os
from datetime import datetime, timedelta
import logging
import config

log = logging.getLogger(__name__)


# ─── Connection helper ────────────────────────────────────────────────────────

def _ensure_data_dir():
    os.makedirs(config.DATA_DIR, exist_ok=True)


def get_conn() -> sqlite3.Connection:
    """Return a new SQLite connection to the helmet database."""
    _ensure_data_dir()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row   # access columns by name
    return conn


def check_and_recover_db() -> None:
    """
    Check SQLite integrity. If the DB is corrupt, rename it (preserves it for
    debugging) and let init_db() create a fresh one on the next call.
    """
    if not os.path.exists(config.DB_PATH):
        return   # nothing to check yet

    try:
        conn = sqlite3.connect(config.DB_PATH)
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        if result and result[0] == "ok":
            return   # healthy
        log.error("[DB] Integrity check FAILED: %s", result)
    except Exception as exc:
        log.error("[DB] Cannot open DB: %s", exc)

    # Rename corrupt file and start fresh
    corrupt_path = config.DB_PATH + ".corrupt"
    try:
        os.rename(config.DB_PATH, corrupt_path)
        log.warning("[DB] Corrupt DB renamed to %s. A fresh DB will be created.", corrupt_path)
    except OSError as e:
        log.error("[DB] Could not rename corrupt DB: %s", e)

# ─── Schema ───────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they do not already exist, and migrate old ones."""
    conn = get_conn()
    c = conn.cursor()

    # Reminders / alarms
    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            message      TEXT    NOT NULL,
            trigger_datetime TEXT    NOT NULL,   -- 'YYYY-MM-DD HH:MM:SS' full datetime
            done         INTEGER DEFAULT 0,  -- 0 = pending, 1 = played
            created_at   TEXT    NOT NULL
        )
    """)

    # Shift / task handover messages
    c.execute("""
        CREATE TABLE IF NOT EXISTS handover (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_name TEXT,               -- parsed from speech or config
            sender_id   TEXT,               -- "worker 1" | "worker 2" | "manager"
            sender_role TEXT    NOT NULL,   -- "manager" | "worker"
            zone        TEXT,               -- location / zone on site
            message     TEXT    NOT NULL,   -- full transcribed text
            timestamp   TEXT    NOT NULL,   -- ISO datetime string
            played_by   TEXT    DEFAULT '',  -- comma-separated HELMET_IDs that read this
            language    TEXT    DEFAULT 'en'
        )
    """)

    _migrate_handover_table(c)
    _migrate_reminders_table(c)

    conn.commit()
    conn.close()
    log.info("[DB] Database ready: %s", config.DB_PATH)


def _migrate_handover_table(c):
    """
    Add new columns to an existing handover table if it was created by an
    older version of this code. Safe to run every startup.
    """
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(handover)").fetchall()}

    if "sender_id" not in existing_cols:
        c.execute("ALTER TABLE handover ADD COLUMN sender_id TEXT")
        log.info("[DB] Migrated: added handover.sender_id")

    if "played_by" not in existing_cols:
        c.execute("ALTER TABLE handover ADD COLUMN played_by TEXT DEFAULT ''")
        log.info("[DB] Migrated: added handover.played_by")

    if "language" not in existing_cols:
        c.execute("ALTER TABLE handover ADD COLUMN language TEXT DEFAULT 'en'")
        log.info("[DB] Migrated: added handover.language")

    if "played" in existing_cols:
        log.info("[DB] Note: legacy 'played' column still present but no longer used by code.")

def _migrate_reminders_table(c):
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(reminders)").fetchall()}

    if "trigger_datetime" in existing_cols:
        log.info("[DB] Migrating reminders table — old rows discarded.")
        c.execute("DROP TABLE reminders")
        c.execute("""
            CREATE TABLE reminders (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                message          TEXT    NOT NULL,
                trigger_datetime TEXT    NOT NULL,
                done             INTEGER DEFAULT 0,
                created_at       TEXT    NOT NULL
            )
        """)
        log.info("[DB] Reminders table recreated with trigger_datetime column.")

def cleanup_old_records(days: int = 7) -> None:
    """
    Delete fired reminders and played handovers older than `days` days.
    Call this once at startup from main.py.
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    c = conn.cursor()

    c.execute("DELETE FROM reminders WHERE done = 1 AND created_at < ?", (cutoff,))
    reminders_deleted = c.rowcount

    c.execute("DELETE FROM handover WHERE played_by IS NOT NULL AND played_by != '' AND timestamp < ?", (cutoff,))
    handover_deleted = c.rowcount

    conn.commit()
    conn.close()
    log.info("[DB] Cleanup: removed %d old reminders, %d old handover entries.",
             reminders_deleted, handover_deleted)