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
    """Create all tables if they do not already exist."""
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
            sender_role TEXT    NOT NULL,   -- "manager" | "worker"
            zone        TEXT,               -- location / zone on site
            message     TEXT    NOT NULL,   -- full transcribed text
            timestamp   TEXT    NOT NULL,   -- ISO datetime string
            played      INTEGER DEFAULT 0   -- 0 = unread, 1 = played
        )
    """)

    conn.commit()
    conn.close()
    log.info("[DB] Database ready: %s", config.DB_PATH)
