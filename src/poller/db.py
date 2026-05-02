"""SQLite layer.

All SQL lives here. Routes and the CLI call functions from this module; they
should never write SQL inline. Each table and each non-trivial query has a
plain-English comment above it. One database file == one session.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

# --- schema -------------------------------------------------------------------

SCHEMA = """
-- One row per session. The questions snapshot is stored as JSON so that
-- changes to the YAML file after the session starts do not silently alter
-- what was asked. Use `--migrate-questions` to opt in to changes.
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    public_url_override TEXT,
    admin_token TEXT NOT NULL
);

-- Tracks which question (if any) is currently active, whether the session
-- has been ended, and whether free-text answers are revealed on /present.
-- Drives the IDLE / QUESTION_ACTIVE / ENDED state machine.
CREATE TABLE IF NOT EXISTS state (
    session_id TEXT PRIMARY KEY,
    active_question_id TEXT,
    ended INTEGER NOT NULL DEFAULT 0,
    reveal_free_text INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- One row per (session, question, participant). The UNIQUE constraint
-- enforces "one answer per participant per question" -- duplicate submits
-- replace the previous answer with INSERT ... ON CONFLICT DO UPDATE.
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    UNIQUE(session_id, question_id, participant_id)
);

-- Heartbeat table. Every participant poll updates last_seen_at via upsert.
-- Used to compute the live "connected" count shown on /admin and /present.
CREATE TABLE IF NOT EXISTS participants (
    session_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (session_id, participant_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Free-text answers the presenter has approved for /present. Admin sees all
-- answers; /present only sees approved ones (and only when the global
-- reveal_free_text toggle is on).
CREATE TABLE IF NOT EXISTS approved_free_text (
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    response_id INTEGER NOT NULL,
    PRIMARY KEY (session_id, question_id, response_id),
    FOREIGN KEY (response_id) REFERENCES responses(id)
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def now_iso() -> str:
    # Uniform ISO-8601 timestamp string used in every created_at / last_seen_at.
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


# --- sessions -----------------------------------------------------------------


def create_session(
    conn: sqlite3.Connection,
    session_id: str,
    title: str,
    questions: list[dict],
    admin_token: str,
) -> None:
    """Insert a new session row plus a fresh state row (IDLE)."""
    conn.execute(
        "INSERT INTO sessions (id, title, created_at, questions_json, admin_token) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, title, now_iso(), json.dumps(questions), admin_token),
    )
    conn.execute("INSERT INTO state (session_id) VALUES (?)", (session_id,))
    conn.commit()


def get_session(conn: sqlite3.Connection) -> dict | None:
    """Return the (only) session in this DB, or None if it has not been created."""
    row = conn.execute(
        "SELECT id, title, created_at, questions_json, public_url_override, admin_token "
        "FROM sessions LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "questions": json.loads(row["questions_json"]),
        "public_url_override": row["public_url_override"],
        "admin_token": row["admin_token"],
    }


def set_public_url_override(
    conn: sqlite3.Connection, session_id: str, override: str | None
) -> None:
    # Empty string -> NULL (unset). Stored on the session row so it survives restart.
    value = override if override else None
    conn.execute(
        "UPDATE sessions SET public_url_override = ? WHERE id = ?",
        (value, session_id),
    )
    conn.commit()


def replace_questions(conn: sqlite3.Connection, session_id: str, questions: list[dict]) -> None:
    """Used by --migrate-questions: overwrite the snapshot."""
    conn.execute(
        "UPDATE sessions SET questions_json = ? WHERE id = ?",
        (json.dumps(questions), session_id),
    )
    conn.commit()


# --- state machine ------------------------------------------------------------


def get_state(conn: sqlite3.Connection, session_id: str) -> dict:
    row = conn.execute(
        "SELECT active_question_id, ended, reveal_free_text FROM state WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return {
        "active_question_id": row["active_question_id"],
        "ended": bool(row["ended"]),
        "reveal_free_text": bool(row["reveal_free_text"]),
    }


def set_active_question(conn: sqlite3.Connection, session_id: str, question_id: str) -> None:
    """Activate a question. Clears `ended` so reactivating from ENDED reopens the session."""
    conn.execute(
        "UPDATE state SET active_question_id = ?, ended = 0 WHERE session_id = ?",
        (question_id, session_id),
    )
    conn.commit()


def clear_active_question(conn: sqlite3.Connection, session_id: str) -> None:
    """Return to IDLE."""
    conn.execute(
        "UPDATE state SET active_question_id = NULL WHERE session_id = ?",
        (session_id,),
    )
    conn.commit()


def end_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Move to ENDED. Active question is also cleared so the participant page
    does not keep showing the last question after it ended."""
    conn.execute(
        "UPDATE state SET ended = 1, active_question_id = NULL WHERE session_id = ?",
        (session_id,),
    )
    conn.commit()


def set_reveal_free_text(conn: sqlite3.Connection, session_id: str, reveal: bool) -> None:
    conn.execute(
        "UPDATE state SET reveal_free_text = ? WHERE session_id = ?",
        (1 if reveal else 0, session_id),
    )
    conn.commit()


# --- responses ----------------------------------------------------------------


def insert_response(
    conn: sqlite3.Connection,
    session_id: str,
    question_id: str,
    participant_id: str,
    answer: str,
) -> None:
    """One answer per participant per question; resubmits replace.

    The ON CONFLICT clause matches the UNIQUE(session_id, question_id, participant_id)
    constraint defined on `responses`.
    """
    conn.execute(
        """
        INSERT INTO responses (session_id, question_id, participant_id, answer, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id, question_id, participant_id) DO UPDATE SET
            answer = excluded.answer,
            created_at = excluded.created_at
        """,
        (session_id, question_id, participant_id, answer, now_iso()),
    )
    conn.commit()


def get_response(
    conn: sqlite3.Connection, session_id: str, question_id: str, participant_id: str
) -> str | None:
    row = conn.execute(
        "SELECT answer FROM responses "
        "WHERE session_id = ? AND question_id = ? AND participant_id = ?",
        (session_id, question_id, participant_id),
    ).fetchone()
    return row["answer"] if row else None


def list_responses(conn: sqlite3.Connection, session_id: str, question_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, participant_id, answer, created_at FROM responses "
        "WHERE session_id = ? AND question_id = ? ORDER BY id ASC",
        (session_id, question_id),
    ).fetchall()
    return [dict(r) for r in rows]


def list_all_responses(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Used by CSV export."""
    rows = conn.execute(
        "SELECT id, question_id, participant_id, answer, created_at FROM responses "
        "WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_responses(conn: sqlite3.Connection, session_id: str, question_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM responses WHERE session_id = ? AND question_id = ?",
        (session_id, question_id),
    ).fetchone()[0]


def aggregate_choice_counts(
    conn: sqlite3.Connection, session_id: str, question_id: str
) -> dict[str, int]:
    """Return {option_id: count}. Caller fills in zeros for options with no votes."""
    rows = conn.execute(
        "SELECT answer, COUNT(*) AS n FROM responses "
        "WHERE session_id = ? AND question_id = ? GROUP BY answer",
        (session_id, question_id),
    ).fetchall()
    return {r["answer"]: r["n"] for r in rows}


# --- heartbeats / connected count ---------------------------------------------


def heartbeat(conn: sqlite3.Connection, session_id: str, participant_id: str) -> None:
    """Upsert this participant's last_seen_at. Called on every participant poll."""
    conn.execute(
        """
        INSERT INTO participants (session_id, participant_id, last_seen_at)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id, participant_id) DO UPDATE SET
            last_seen_at = excluded.last_seen_at
        """,
        (session_id, participant_id, now_iso()),
    )
    conn.commit()


def count_connected(conn: sqlite3.Connection, session_id: str, window_seconds: int = 30) -> int:
    """Distinct participants whose last poll was within the last `window_seconds`.

    The window is intentionally a few times longer than the participant poll
    interval (1-2s) so a single missed poll does not drop someone off the count.
    """
    threshold = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - window_seconds)) + "Z"
    return conn.execute(
        "SELECT COUNT(*) FROM participants WHERE session_id = ? AND last_seen_at > ?",
        (session_id, threshold),
    ).fetchone()[0]


def count_answered(conn: sqlite3.Connection, session_id: str, question_id: str) -> int:
    """Distinct participants who have answered the given question."""
    return conn.execute(
        "SELECT COUNT(DISTINCT participant_id) FROM responses "
        "WHERE session_id = ? AND question_id = ?",
        (session_id, question_id),
    ).fetchone()[0]


# --- free-text moderation -----------------------------------------------------


def approve_free_text(
    conn: sqlite3.Connection, session_id: str, question_id: str, response_id: int
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO approved_free_text (session_id, question_id, response_id) "
        "VALUES (?, ?, ?)",
        (session_id, question_id, response_id),
    )
    conn.commit()


def unapprove_free_text(
    conn: sqlite3.Connection, session_id: str, question_id: str, response_id: int
) -> None:
    conn.execute(
        "DELETE FROM approved_free_text "
        "WHERE session_id = ? AND question_id = ? AND response_id = ?",
        (session_id, question_id, response_id),
    )
    conn.commit()


def is_approved(
    conn: sqlite3.Connection, session_id: str, question_id: str, response_id: int
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM approved_free_text "
        "WHERE session_id = ? AND question_id = ? AND response_id = ?",
        (session_id, question_id, response_id),
    ).fetchone()
    return row is not None


def list_approved_free_text(
    conn: sqlite3.Connection, session_id: str, question_id: str
) -> list[dict]:
    """Approved free-text answers for /present, oldest first."""
    rows = conn.execute(
        """
        SELECT r.id, r.answer, r.created_at
        FROM approved_free_text a
        JOIN responses r ON r.id = a.response_id
        WHERE a.session_id = ? AND a.question_id = ?
        ORDER BY r.id ASC
        """,
        (session_id, question_id),
    ).fetchall()
    return [dict(r) for r in rows]
