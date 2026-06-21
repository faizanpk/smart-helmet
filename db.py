# db.py – Centralised SQLite database initialisation and helper functions.
# All modules import from here instead of duplicating DB logic.

import sqlite3
import os
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
            trigger_time TEXT    NOT NULL,   -- 'HH:MM' 24-hour format
            done         INTEGER DEFAULT 0,  -- 0 = pending, 1 = played
            created_at   TEXT    NOT NULL
        )
    """)

    # Shift / task handover messages
    c.execute("""
        CREATE TABLE IF NOT EXISTS handover (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_name TEXT,               -- parsed from speech or config
            sender_id   TEXT,               -- "w-01" | "w-02" | "manager"
            sender_role TEXT    NOT NULL,   -- "manager" | "worker"
            zone        TEXT,               -- location / zone on site
            message     TEXT    NOT NULL,   -- full transcribed text
            timestamp   TEXT    NOT NULL,   -- ISO datetime string
            played_by   TEXT    DEFAULT ''  -- comma-separated HELMET_IDs that read this
        )
    """)

    _migrate_handover_table(c)

    conn.commit()
    conn.close()
    log.info("[DB] Database ready: %s", config.DB_PATH)


def _migrate_handover_table(c):
    """
    Add new columns to an existing handover table if it was created by an
    older version of this code (before sender_id/played_by existed).
    Safe to run every startup — does nothing if columns already exist.
    """
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(handover)").fetchall()}

    if "sender_id" not in existing_cols:
        c.execute("ALTER TABLE handover ADD COLUMN sender_id TEXT")
        log.info("[DB] Migrated: added handover.sender_id")

    if "played_by" not in existing_cols:
        c.execute("ALTER TABLE handover ADD COLUMN played_by TEXT DEFAULT ''")
        log.info("[DB] Migrated: added handover.played_by")

    if "played" in existing_cols:
        log.info("[DB] Note: legacy 'played' column still present but no longer used by code.")