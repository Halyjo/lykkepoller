"""SQLite layer.

All SQL lives here. Routes and the CLI call functions from this module; they
should never write SQL inline. Each table and each non-trivial query has a
plain-English comment above it. One database file == one session.

Two response tables, by design:

  unique_responses  -- one row per (session, question, participant). Used
                       for MC (and any future "one answer per person" type
                       like ratings). The UNIQUE constraint plus
                       INSERT ... ON CONFLICT DO NOTHING enforces the
                       no-changing-answers rule from issue #5.

  append_responses  -- append-only log. Used for free-text, where a
                       participant may submit several answers in a row
                       (issue #6). No UNIQUE constraint.

Splitting into two tables keeps each query simple and small (no `WHERE
question_type = ...` everywhere) and lets the row's *table* be the source
of truth for "is this answer one-shot or append-only" rather than a
column that could disagree with the question definition.
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
-- source_yaml_filename records the basename of the YAML the session was
-- created from (e.g. "questions.yaml"); shown by `lykkepoller inspect` so the
-- DB file's origin is recoverable without opening the snapshot.
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    public_url_override TEXT,
    admin_token TEXT NOT NULL,
    source_yaml_filename TEXT,
    -- Ordered deck spine. JSON list of slide entries:
    --   {type: "question", question_id: "..."} or
    --   {type: "content",  source: "slides/01.html", html: "<rendered html>"}
    -- For legacy questions-only YAMLs, this is synthesized as one
    -- question slide per question; new "slides:" YAMLs interleave content
    -- and questions in author-defined order.
    slides_json TEXT,
    -- Absolute path to the talk directory, used to mount /talk/* for slide
    -- assets (images, theme.css). NULL for legacy questions-only sessions.
    talk_dir TEXT
);

-- Tracks which question (if any) is currently active, whether the session
-- has been ended, and the two presenter "reveal" toggles for /present:
--   reveal_free_text -- "show audience-visible result details": for free-text
--                       questions this means the approved-answers list; for MC
--                       this means the bar chart with counts/percentages.
--                       (The column name is historical -- it predates MC bars
--                       being gated on the same flag.)
--   reveal_correct   -- color the correct option(s) of the active MC
--                       question. Implies bars are visible (otherwise the
--                       coloring has nothing to land on).
-- Drives the IDLE / QUESTION_ACTIVE / ENDED state machine.
CREATE TABLE IF NOT EXISTS state (
    session_id TEXT PRIMARY KEY,
    active_question_id TEXT,
    ended INTEGER NOT NULL DEFAULT 0,
    reveal_free_text INTEGER NOT NULL DEFAULT 0,
    reveal_correct INTEGER NOT NULL DEFAULT 0,
    -- Index into sessions.slides_json. Drives /admin next/prev navigation
    -- through the deck. NULL when idle. For a question slide, also implies
    -- active_question_id is set; for a content slide, active_question_id is
    -- NULL.
    active_slide_index INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- One row per (session, question, participant). UNIQUE + INSERT ON
-- CONFLICT DO NOTHING enforces "answer once, no changes" for MC questions
-- (issue #5).
CREATE TABLE IF NOT EXISTS unique_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    UNIQUE(session_id, question_id, participant_id)
);

-- Append-only log of free-text answers. A participant can submit as many
-- times as they want for the same question (issue #6); each submit is a
-- new row with its own id, which is what approved_free_text references.
CREATE TABLE IF NOT EXISTS append_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
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
    FOREIGN KEY (response_id) REFERENCES append_responses(id)
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
    # SQLite's CREATE TABLE IF NOT EXISTS does not retro-add new columns to
    # tables created by an earlier version. Anything added to SCHEMA after
    # v0.1.0 needs an explicit ALTER TABLE here so old DBs pick it up on reopen.
    _ensure_column(conn, "sessions", "source_yaml_filename", "TEXT")
    _ensure_column(conn, "state", "reveal_correct", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "sessions", "slides_json", "TEXT")
    _ensure_column(conn, "sessions", "talk_dir", "TEXT")
    _ensure_column(conn, "state", "active_slide_index", "INTEGER")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, type_decl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_decl}")


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
    source_yaml_filename: str | None = None,
    slides: list[dict] | None = None,
    talk_dir: str | None = None,
) -> None:
    """Insert a new session row plus a fresh state row (IDLE).

    `slides` is the rendered deck spine; `talk_dir` is the absolute path the
    app mounts at /talk/* for slide assets. Both are optional for legacy
    questions-only sessions; the app synthesizes a slides list from questions
    in that case so the rest of the code can speak slides uniformly.
    """
    conn.execute(
        "INSERT INTO sessions (id, title, created_at, questions_json, "
        "admin_token, source_yaml_filename, slides_json, talk_dir) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            title,
            now_iso(),
            json.dumps(questions),
            admin_token,
            source_yaml_filename,
            json.dumps(slides) if slides is not None else None,
            talk_dir,
        ),
    )
    conn.execute("INSERT INTO state (session_id) VALUES (?)", (session_id,))
    conn.commit()


def get_session(conn: sqlite3.Connection) -> dict | None:
    """Return the (only) session in this DB, or None if it has not been created.

    `slides` is always a list -- if the row has no slides_json (legacy
    questions-only session), we synthesize one question slide per question so
    the rest of the app can speak slides uniformly.
    """
    row = conn.execute(
        "SELECT id, title, created_at, questions_json, public_url_override, "
        "admin_token, source_yaml_filename, slides_json, talk_dir "
        "FROM sessions LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    questions = json.loads(row["questions_json"])
    slides_raw = row["slides_json"]
    if slides_raw:
        slides = json.loads(slides_raw)
    else:
        slides = [{"type": "question", "question_id": q["id"]} for q in questions]
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "questions": questions,
        "public_url_override": row["public_url_override"],
        "admin_token": row["admin_token"],
        "source_yaml_filename": row["source_yaml_filename"],
        "slides": slides,
        "talk_dir": row["talk_dir"],
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
    """Used by --migrate-questions: overwrite the snapshot.

    The deck is kept in step. A question the migration adds has no slide of
    its own, so it would be invisible on /admin (the deck list is built from
    slides) and unreachable with next/prev. We append a question slide for
    each such question, at the end of the deck, in migration order. Slides
    that were already there keep their position.
    """
    conn.execute(
        "UPDATE sessions SET questions_json = ? WHERE id = ?",
        (json.dumps(questions), session_id),
    )
    row = conn.execute(
        "SELECT slides_json FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is not None and row["slides_json"]:
        slides = json.loads(row["slides_json"])
        on_a_slide = {
            s["question_id"] for s in slides if s.get("type") == "question"
        }
        for q in questions:
            if q["id"] not in on_a_slide:
                slides.append({"type": "question", "question_id": q["id"]})
        conn.execute(
            "UPDATE sessions SET slides_json = ? WHERE id = ?",
            (json.dumps(slides), session_id),
        )
    conn.commit()


# --- state machine ------------------------------------------------------------


def get_state(conn: sqlite3.Connection, session_id: str) -> dict:
    row = conn.execute(
        "SELECT active_question_id, ended, reveal_free_text, reveal_correct, "
        "active_slide_index FROM state WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return {
        "active_question_id": row["active_question_id"],
        "ended": bool(row["ended"]),
        "reveal_free_text": bool(row["reveal_free_text"]),
        "reveal_correct": bool(row["reveal_correct"]),
        "active_slide_index": row["active_slide_index"],
    }


def set_active_question(
    conn: sqlite3.Connection,
    session_id: str,
    question_id: str,
    slide_index: int | None = None,
) -> None:
    """Activate a question.

    Side effects:
      - Clears `ended` so reactivating from ENDED reopens the session.
      - Resets both reveal flags so each new question starts with nothing shown.
      - Sets active_slide_index when provided (caller looks up the slide that
        carries this question and passes its index).
    """
    conn.execute(
        "UPDATE state SET active_question_id = ?, ended = 0, "
        "reveal_correct = 0, reveal_free_text = 0, active_slide_index = ? "
        "WHERE session_id = ?",
        (question_id, slide_index, session_id),
    )
    conn.commit()


def set_active_slide_question(
    conn: sqlite3.Connection,
    session_id: str,
    slide_index: int,
    question_id: str,
) -> None:
    """Activate a question slide. Sets both indexes, resets reveal flags
    (each new question starts hidden), and clears the ended flag."""
    conn.execute(
        "UPDATE state SET active_slide_index = ?, active_question_id = ?, "
        "ended = 0, reveal_correct = 0, reveal_free_text = 0 "
        "WHERE session_id = ?",
        (slide_index, question_id, session_id),
    )
    conn.commit()


def set_active_slide_content(
    conn: sqlite3.Connection,
    session_id: str,
    slide_index: int,
) -> None:
    """Activate a content slide.

    Deliberately leaves `active_question_id` and the reveal flags untouched
    so the participant page stays on the most recent question (people often
    keep answering during discussion slides) and the presenter's reveal of
    a result chart on /admin doesn't blink off when they advance to the
    discussion slide that interprets that chart. Use clear_active_question
    or end_session to explicitly close out the prior question.
    """
    conn.execute(
        "UPDATE state SET active_slide_index = ?, ended = 0 "
        "WHERE session_id = ?",
        (slide_index, session_id),
    )
    conn.commit()


def clear_active_question(conn: sqlite3.Connection, session_id: str) -> None:
    """Return to IDLE: drop both the active question and the slide index."""
    conn.execute(
        "UPDATE state SET active_question_id = NULL, active_slide_index = NULL "
        "WHERE session_id = ?",
        (session_id,),
    )
    conn.commit()


def end_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Move to ENDED. Active question and slide are cleared so the participant
    page does not keep showing the last question after it ended."""
    conn.execute(
        "UPDATE state SET ended = 1, active_question_id = NULL, "
        "active_slide_index = NULL WHERE session_id = ?",
        (session_id,),
    )
    conn.commit()


def set_reveal_free_text(conn: sqlite3.Connection, session_id: str, reveal: bool) -> None:
    conn.execute(
        "UPDATE state SET reveal_free_text = ? WHERE session_id = ?",
        (1 if reveal else 0, session_id),
    )
    conn.commit()


def set_reveal_correct(conn: sqlite3.Connection, session_id: str, reveal: bool) -> None:
    conn.execute(
        "UPDATE state SET reveal_correct = ? WHERE session_id = ?",
        (1 if reveal else 0, session_id),
    )
    conn.commit()


# --- responses ----------------------------------------------------------------


def record_unique_answer(
    conn: sqlite3.Connection,
    session_id: str,
    question_id: str,
    participant_id: str,
    answer: str,
) -> bool:
    """Used by MC. One answer per (session, question, participant); a second
    submit is silently dropped (issue #5: no changing answers).

    Returns True if this submit was accepted, False if the participant had
    already answered.
    """
    cur = conn.execute(
        """
        INSERT INTO unique_responses
            (session_id, question_id, participant_id, answer, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id, question_id, participant_id) DO NOTHING
        """,
        (session_id, question_id, participant_id, answer, now_iso()),
    )
    conn.commit()
    return cur.rowcount == 1


def append_text_answer(
    conn: sqlite3.Connection,
    session_id: str,
    question_id: str,
    participant_id: str,
    answer: str,
) -> None:
    """Used by free text. Each submit appends a new row -- a single
    participant can keep submitting (issue #6, e.g. multi-question Q&A or
    feedback streams)."""
    conn.execute(
        """
        INSERT INTO append_responses
            (session_id, question_id, participant_id, answer, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, question_id, participant_id, answer, now_iso()),
    )
    conn.commit()


def get_unique_answer(
    conn: sqlite3.Connection, session_id: str, question_id: str, participant_id: str
) -> str | None:
    """Return the participant's recorded MC answer, or None.

    The participant page uses this to (a) pre-tick the radio they picked and
    (b) hide the form so they cannot try to change it.
    """
    row = conn.execute(
        "SELECT answer FROM unique_responses "
        "WHERE session_id = ? AND question_id = ? AND participant_id = ?",
        (session_id, question_id, participant_id),
    ).fetchone()
    return row["answer"] if row else None


def participant_text_count(
    conn: sqlite3.Connection, session_id: str, question_id: str, participant_id: str
) -> int:
    """How many free-text answers this participant has submitted for this
    question. Drives the "Your answer is recorded -- submit another if you
    like" hint on the participant page."""
    return conn.execute(
        "SELECT COUNT(*) FROM append_responses "
        "WHERE session_id = ? AND question_id = ? AND participant_id = ?",
        (session_id, question_id, participant_id),
    ).fetchone()[0]


def list_text_responses(conn: sqlite3.Connection, session_id: str, question_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, participant_id, answer, created_at FROM append_responses "
        "WHERE session_id = ? AND question_id = ? ORDER BY id ASC",
        (session_id, question_id),
    ).fetchall()
    return [dict(r) for r in rows]


def list_all_responses(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Used by CSV export. Returns the union of both tables ordered by
    insertion. The caller (exports.py) figures out the question type from the
    questions snapshot, so we don't tag rows with type here."""
    rows = conn.execute(
        """
        SELECT id, question_id, participant_id, answer, created_at
        FROM unique_responses WHERE session_id = ?
        UNION ALL
        SELECT id, question_id, participant_id, answer, created_at
        FROM append_responses WHERE session_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (session_id, session_id),
    ).fetchall()
    return [dict(r) for r in rows]


def count_responses(conn: sqlite3.Connection, session_id: str, question_id: str) -> int:
    """Total submissions for this question. For an MC question only
    unique_responses contributes; for a free-text question only
    append_responses does. Summing both is correct in either case."""
    n_unique = conn.execute(
        "SELECT COUNT(*) FROM unique_responses WHERE session_id = ? AND question_id = ?",
        (session_id, question_id),
    ).fetchone()[0]
    n_append = conn.execute(
        "SELECT COUNT(*) FROM append_responses WHERE session_id = ? AND question_id = ?",
        (session_id, question_id),
    ).fetchone()[0]
    return n_unique + n_append


def aggregate_choice_counts(
    conn: sqlite3.Connection, session_id: str, question_id: str
) -> dict[str, int]:
    """Return {option_id: count}. Caller fills in zeros for options with no votes."""
    rows = conn.execute(
        "SELECT answer, COUNT(*) AS n FROM unique_responses "
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
    """Distinct participants who have answered the given question.

    For MC the answer is in unique_responses; for free text, in
    append_responses. We count distinct participants in each table separately
    -- only one of them will be non-zero for any given question, so summing is
    safe and simpler than dispatching on question type here."""
    n_unique = conn.execute(
        "SELECT COUNT(DISTINCT participant_id) FROM unique_responses "
        "WHERE session_id = ? AND question_id = ?",
        (session_id, question_id),
    ).fetchone()[0]
    n_append = conn.execute(
        "SELECT COUNT(DISTINCT participant_id) FROM append_responses "
        "WHERE session_id = ? AND question_id = ?",
        (session_id, question_id),
    ).fetchone()[0]
    return n_unique + n_append


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


def approve_all_existing_free_text(
    conn: sqlite3.Connection, session_id: str, question_id: str
) -> int:
    """Approve every response that already exists for this question.

    Idempotent (INSERT OR IGNORE), so the presenter can press 'A' again after
    a few new answers come in -- previously-approved ones stay, new ones get
    approved, none of them get duplicated. Returns the resulting approval row
    count for this question, mostly for tests.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO approved_free_text (session_id, question_id, response_id)
        SELECT ?, ?, id FROM append_responses
        WHERE session_id = ? AND question_id = ?
        """,
        (session_id, question_id, session_id, question_id),
    )
    conn.commit()
    return conn.execute(
        "SELECT COUNT(*) FROM approved_free_text WHERE session_id = ? AND question_id = ?",
        (session_id, question_id),
    ).fetchone()[0]


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
        JOIN append_responses r ON r.id = a.response_id
        WHERE a.session_id = ? AND a.question_id = ?
        ORDER BY r.id ASC
        """,
        (session_id, question_id),
    ).fetchall()
    return [dict(r) for r in rows]
