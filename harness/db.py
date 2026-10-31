"""SQLite state: runs, proposals, check-ins, activity, cursors, notifications."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

# Append-only: index i migrates user_version i -> i+1.
MIGRATIONS = [
    """
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        agent TEXT NOT NULL,
        model TEXT,
        started TEXT NOT NULL,
        ended TEXT,
        status TEXT NOT NULL,
        turns INTEGER,
        cost_usd REAL,
        session_id TEXT,
        error TEXT
    );
    CREATE TABLE proposals (
        id TEXT PRIMARY KEY,
        agent TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT,
        payload TEXT NOT NULL,
        preview TEXT,
        reason TEXT,
        undo TEXT,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'approved', 'rejected', 'executed', 'failed')),
        created TEXT NOT NULL,
        decided TEXT,
        executed TEXT,
        result TEXT
    );
    CREATE TABLE checkins (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        mood INTEGER CHECK (mood BETWEEN 1 AND 5),
        energy INTEGER CHECK (energy BETWEEN 1 AND 5),
        stress INTEGER CHECK (stress BETWEEN 1 AND 5),
        note TEXT,
        source TEXT
    );
    CREATE TABLE activity (
        id INTEGER PRIMARY KEY,
        session_id TEXT UNIQUE,
        project TEXT,
        started TEXT,
        ended TEXT,
        source TEXT
    );
    CREATE TABLE cursors (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated TEXT NOT NULL
    );
    CREATE TABLE notifications (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT,
        priority INTEGER,
        sent INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX proposals_status ON proposals(status);
    CREATE INDEX runs_agent_started ON runs(agent, started);
    """,
    """
    CREATE TABLE worklog (
        day TEXT PRIMARY KEY,
        planned TEXT,
        actual TEXT,
        updated TEXT NOT NULL,
        synced TEXT
    );
    """,
    """
    ALTER TABLE proposals ADD COLUMN level TEXT;
    """,
]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i in range(version, len(MIGRATIONS)):
        with conn:
            conn.executescript(MIGRATIONS[i])
            conn.execute(f"PRAGMA user_version = {i + 1}")
    return len(MIGRATIONS)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def record_run(conn, *, agent: str, model: str | None, started: str, result) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO runs (agent, model, started, ended, status, turns, cost_usd, session_id, error)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (agent, model, started, now_iso(), "ok" if result.ok else "error",
             result.turns, result.cost_usd, result.session_id, result.error),
        )
    return cur.lastrowid


def save_worklog(conn, day: str, *, planned: dict | None = None, actual: dict | None = None) -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO worklog (day, updated) VALUES (?, ?)", (day, now_iso()))
        if planned is not None:
            conn.execute("UPDATE worklog SET planned = ?, updated = ?, synced = NULL WHERE day = ?",
                         (json.dumps(planned), now_iso(), day))
        if actual is not None:
            conn.execute("UPDATE worklog SET actual = ?, updated = ?, synced = NULL WHERE day = ?",
                         (json.dumps(actual), now_iso(), day))


def get_worklog(conn, day: str) -> tuple[dict, dict]:
    row = conn.execute("SELECT planned, actual FROM worklog WHERE day = ?", (day,)).fetchone()
    if not row:
        return {}, {}
    return json.loads(row["planned"] or "{}"), json.loads(row["actual"] or "{}")


def is_paused(conn) -> bool:
    return get_cursor(conn, "paused") == "1"


def get_cursor(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM cursors WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_cursor(conn, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO cursors (key, value, updated) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
            (key, value, now_iso()),
        )
