"""Database tests.

These are deliberately exhaustive. The DB layer is the single source of truth
for session state, and writing this stuff in plain SQL means we want a thick
test belt around it.
"""

import sqlite3
import time

import pytest

from lykkepoller import db


def make_qs():
    return [
        {
            "id": "q1",
            "type": "multiple_choice",
            "prompt": "Pick a fruit",
            "options": [{"id": "A", "label": "Apple"}, {"id": "B", "label": "Banana"}],
        },
        {"id": "q2", "type": "free_text", "prompt": "Why?"},
    ]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "s.sqlite")
    db.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def session(conn):
    db.create_session(conn, "blue-otter-1234", "Demo", make_qs(), "secret-token")
    return "blue-otter-1234"


# --- schema + session ---------------------------------------------------------


def test_init_schema_idempotent(tmp_path):
    c = db.connect(tmp_path / "s.sqlite")
    db.init_schema(c)
    db.init_schema(c)  # should not raise
    rows = c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    names = [r["name"] for r in rows]
    assert {"sessions", "state", "responses", "participants", "approved_free_text"}.issubset(
        set(names)
    )


def test_create_and_read_session(conn, session):
    s = db.get_session(conn)
    assert s["id"] == session
    assert s["title"] == "Demo"
    assert s["admin_token"] == "secret-token"
    assert s["public_url_override"] is None
    # questions are stored as JSON; round-trip must be lossless
    assert s["questions"] == make_qs()


def test_get_session_empty_db(conn):
    assert db.get_session(conn) is None


def test_initial_state_is_idle(conn, session):
    st = db.get_state(conn, session)
    assert st == {
        "active_question_id": None,
        "ended": False,
        "reveal_free_text": False,
        "reveal_correct": False,
    }


def test_set_public_url_override(conn, session):
    db.set_public_url_override(conn, session, "https://abc.example")
    assert db.get_session(conn)["public_url_override"] == "https://abc.example"
    db.set_public_url_override(conn, session, "")
    assert db.get_session(conn)["public_url_override"] is None


# --- state machine ------------------------------------------------------------


def test_set_and_clear_active_question(conn, session):
    db.set_active_question(conn, session, "q1")
    assert db.get_state(conn, session)["active_question_id"] == "q1"
    db.set_active_question(conn, session, "q2")
    assert db.get_state(conn, session)["active_question_id"] == "q2"
    db.clear_active_question(conn, session)
    assert db.get_state(conn, session)["active_question_id"] is None


def test_end_session_and_reopen(conn, session):
    db.set_active_question(conn, session, "q1")
    db.end_session(conn, session)
    st = db.get_state(conn, session)
    assert st["ended"] is True
    assert st["active_question_id"] is None
    # Activating any question reopens (clears `ended`).
    db.set_active_question(conn, session, "q2")
    st = db.get_state(conn, session)
    assert st["ended"] is False
    assert st["active_question_id"] == "q2"


def test_set_reveal_free_text(conn, session):
    db.set_reveal_free_text(conn, session, True)
    assert db.get_state(conn, session)["reveal_free_text"] is True
    db.set_reveal_free_text(conn, session, False)
    assert db.get_state(conn, session)["reveal_free_text"] is False


def test_set_reveal_correct(conn, session):
    db.set_reveal_correct(conn, session, True)
    assert db.get_state(conn, session)["reveal_correct"] is True
    db.set_reveal_correct(conn, session, False)
    assert db.get_state(conn, session)["reveal_correct"] is False


def test_activating_question_resets_reveal_correct(conn, session):
    # Each new question should start with the correct-answer reveal hidden,
    # so pressing Next does not leak the next question's answer.
    db.set_active_question(conn, session, "q1")
    db.set_reveal_correct(conn, session, True)
    assert db.get_state(conn, session)["reveal_correct"] is True
    db.set_active_question(conn, session, "q2")
    assert db.get_state(conn, session)["reveal_correct"] is False


def test_create_session_records_yaml_filename(conn):
    db.create_session(
        conn,
        "wild-fox-9999",
        "Demo",
        make_qs(),
        "tok",
        source_yaml_filename="SAR-presentation.yaml",
    )
    s = db.get_session(conn)
    assert s["source_yaml_filename"] == "SAR-presentation.yaml"


def test_init_schema_migrates_old_db(tmp_path):
    """Reopening a v0.1.0 DB (no source_yaml_filename, no reveal_correct)
    should not crash; the columns get added on init_schema."""
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            questions_json TEXT NOT NULL,
            public_url_override TEXT,
            admin_token TEXT NOT NULL
        );
        CREATE TABLE state (
            session_id TEXT PRIMARY KEY,
            active_question_id TEXT,
            ended INTEGER NOT NULL DEFAULT 0,
            reveal_free_text INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO sessions (id, title, created_at, questions_json, admin_token)
            VALUES ('blue-otter-1234', 'Old', '2026-01-01T00:00:00Z', '[]', 'tok');
        INSERT INTO state (session_id) VALUES ('blue-otter-1234');
        """
    )
    c.commit()
    c.close()

    c2 = db.connect(p)
    db.init_schema(c2)
    s = db.get_session(c2)
    assert s["id"] == "blue-otter-1234"
    assert s["source_yaml_filename"] is None
    st = db.get_state(c2, "blue-otter-1234")
    assert st["reveal_correct"] is False
    c2.close()


# --- responses ----------------------------------------------------------------


def test_insert_response_and_read_it_back(conn, session):
    db.insert_response(conn, session, "q1", "p-alice", "A")
    assert db.get_response(conn, session, "q1", "p-alice") == "A"
    rows = db.list_responses(conn, session, "q1")
    assert len(rows) == 1 and rows[0]["answer"] == "A"


def test_resubmit_replaces_previous_answer(conn, session):
    db.insert_response(conn, session, "q1", "p-alice", "A")
    db.insert_response(conn, session, "q1", "p-alice", "B")
    rows = db.list_responses(conn, session, "q1")
    assert len(rows) == 1
    assert rows[0]["answer"] == "B"


def test_different_participants_can_answer_same_question(conn, session):
    db.insert_response(conn, session, "q1", "p-alice", "A")
    db.insert_response(conn, session, "q1", "p-bob", "B")
    db.insert_response(conn, session, "q1", "p-carol", "A")
    counts = db.aggregate_choice_counts(conn, session, "q1")
    assert counts == {"A": 2, "B": 1}


def test_same_participant_can_answer_different_questions(conn, session):
    db.insert_response(conn, session, "q1", "p-alice", "A")
    db.insert_response(conn, session, "q2", "p-alice", "because reasons")
    assert db.get_response(conn, session, "q1", "p-alice") == "A"
    assert db.get_response(conn, session, "q2", "p-alice") == "because reasons"


def test_count_responses(conn, session):
    db.insert_response(conn, session, "q1", "p-alice", "A")
    db.insert_response(conn, session, "q1", "p-bob", "B")
    assert db.count_responses(conn, session, "q1") == 2
    assert db.count_responses(conn, session, "q2") == 0


def test_count_answered_distinct(conn, session):
    db.insert_response(conn, session, "q1", "p-alice", "A")
    # Same participant resubmitting must not be double-counted.
    db.insert_response(conn, session, "q1", "p-alice", "B")
    db.insert_response(conn, session, "q1", "p-bob", "B")
    assert db.count_answered(conn, session, "q1") == 2


# --- heartbeats / connected count ---------------------------------------------


def test_heartbeat_records_a_participant(conn, session):
    db.heartbeat(conn, session, "p-alice")
    assert db.count_connected(conn, session, window_seconds=30) == 1


def test_heartbeat_upserts(conn, session):
    db.heartbeat(conn, session, "p-alice")
    db.heartbeat(conn, session, "p-alice")
    assert db.count_connected(conn, session, window_seconds=30) == 1


def test_count_connected_excludes_stale(conn, session):
    db.heartbeat(conn, session, "p-alice")
    db.heartbeat(conn, session, "p-bob")
    # Only p-bob should appear in the very-recent window.
    assert db.count_connected(conn, session, window_seconds=30) == 2
    # Backdate p-alice past the window directly via SQL.
    long_ago = "2000-01-01T00:00:00Z"
    conn.execute(
        "UPDATE participants SET last_seen_at = ? WHERE participant_id = 'p-alice'",
        (long_ago,),
    )
    conn.commit()
    assert db.count_connected(conn, session, window_seconds=30) == 1


def test_count_connected_window(conn, session):
    db.heartbeat(conn, session, "p-alice")
    time.sleep(1.2)
    db.heartbeat(conn, session, "p-bob")
    # Both seen within ~2s, but only p-bob within ~1s.
    assert db.count_connected(conn, session, window_seconds=5) == 2


# --- free-text moderation -----------------------------------------------------


def test_approve_all_existing_free_text(conn, session):
    db.insert_response(conn, session, "q2", "p-alice", "first")
    db.insert_response(conn, session, "q2", "p-bob", "second")
    db.insert_response(conn, session, "q2", "p-carol", "third")
    n = db.approve_all_existing_free_text(conn, session, "q2")
    assert n == 3
    assert {a["answer"] for a in db.list_approved_free_text(conn, session, "q2")} == {
        "first",
        "second",
        "third",
    }


def test_approve_all_is_idempotent_and_picks_up_new(conn, session):
    db.insert_response(conn, session, "q2", "p-alice", "first")
    db.approve_all_existing_free_text(conn, session, "q2")
    # New answer arrives
    db.insert_response(conn, session, "q2", "p-bob", "second")
    # Second press picks up the new one without duplicating the first.
    n = db.approve_all_existing_free_text(conn, session, "q2")
    assert n == 2


def test_approve_all_only_affects_target_question(conn, session):
    db.insert_response(conn, session, "q1", "p-alice", "A")
    db.insert_response(conn, session, "q2", "p-alice", "free text")
    db.approve_all_existing_free_text(conn, session, "q2")
    # q1 (a multiple-choice question) should have no approval rows.
    assert db.list_approved_free_text(conn, session, "q1") == []


def test_approve_and_unapprove_free_text(conn, session):
    db.insert_response(conn, session, "q2", "p-alice", "first")
    db.insert_response(conn, session, "q2", "p-bob", "second")
    rows = db.list_responses(conn, session, "q2")
    rid_alice = next(r["id"] for r in rows if r["participant_id"] == "p-alice")

    assert db.list_approved_free_text(conn, session, "q2") == []
    db.approve_free_text(conn, session, "q2", rid_alice)
    assert db.is_approved(conn, session, "q2", rid_alice)
    approved = db.list_approved_free_text(conn, session, "q2")
    assert len(approved) == 1 and approved[0]["answer"] == "first"

    # Idempotent: approving twice does not duplicate.
    db.approve_free_text(conn, session, "q2", rid_alice)
    assert len(db.list_approved_free_text(conn, session, "q2")) == 1

    db.unapprove_free_text(conn, session, "q2", rid_alice)
    assert not db.is_approved(conn, session, "q2", rid_alice)
    assert db.list_approved_free_text(conn, session, "q2") == []


# --- reopen / restart ---------------------------------------------------------


def test_reopen_existing_database_preserves_state(tmp_path):
    path = tmp_path / "s.sqlite"
    # First open: create the session, set state and a response.
    c1 = db.connect(path)
    db.init_schema(c1)
    db.create_session(c1, "blue-otter-1234", "Demo", make_qs(), "tok")
    db.set_active_question(c1, "blue-otter-1234", "q2")
    db.insert_response(c1, "blue-otter-1234", "q1", "p-alice", "A")
    c1.close()
    # Reopen: same session id, same admin token, same active question, same answers.
    c2 = db.connect(path)
    s = db.get_session(c2)
    assert s["id"] == "blue-otter-1234"
    assert s["admin_token"] == "tok"
    assert db.get_state(c2, "blue-otter-1234")["active_question_id"] == "q2"
    assert db.get_response(c2, "blue-otter-1234", "q1", "p-alice") == "A"
    c2.close()


def test_replace_questions_overwrites_snapshot(conn, session):
    new_qs = [{"id": "q1", "type": "free_text", "prompt": "renamed"}]
    db.replace_questions(conn, session, new_qs)
    assert db.get_session(conn)["questions"] == new_qs
