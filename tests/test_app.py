"""HTTP-level integration tests using FastAPI's TestClient."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poller import app as app_module
from poller import db


def make_qs():
    return [
        {
            "id": "q1",
            "type": "multiple_choice",
            "prompt": "Pick a fruit",
            "options": [{"id": "A", "label": "Apple"}, {"id": "B", "label": "Banana"}],
        },
        {"id": "q2", "type": "free_text", "prompt": "Why?"},
        {
            "id": "q3",
            "type": "multiple_choice",
            "prompt": "Color",
            "options": [{"id": "r", "label": "red"}, {"id": "b", "label": "blue"}],
        },
    ]


@pytest.fixture
def app_client(tmp_path: Path):
    p = tmp_path / "s.sqlite"
    c = db.connect(p)
    db.init_schema(c)
    db.create_session(c, "blue-otter-1234", "Demo", make_qs(), "secret")
    c.close()
    a = app_module.create_app(db_path=p)
    return TestClient(a, follow_redirects=False)


def admin(client):
    """Return a TestClient that has the admin cookie set."""
    r = client.get("/admin?token=secret")
    assert r.status_code == 303
    # TestClient persists cookies on the same client instance.
    return client


# --- public pages -------------------------------------------------------------


def test_join_idle(app_client):
    r = app_client.get("/join")
    assert r.status_code == 200
    assert "Waiting for presenter" in r.text


def test_admin_requires_token(app_client):
    r = app_client.get("/admin")
    assert r.status_code == 401


def test_admin_bad_token(app_client):
    r = app_client.get("/admin?token=nope")
    assert r.status_code == 401


def test_admin_token_sets_cookie_then_clean_url(app_client):
    r = app_client.get("/admin?token=secret")
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"
    # cookie set on the TestClient session
    r2 = app_client.get("/admin")
    assert r2.status_code == 200
    assert "Demo" in r2.text


# --- state machine ------------------------------------------------------------


def test_activate_question_via_post(app_client):
    admin(app_client)
    r = app_client.post("/admin/activate", data={"qid": "q1"})
    assert r.status_code == 303
    r = app_client.get("/admin")
    assert "Pick a fruit" in r.text
    # /join now shows the active question
    r = app_client.get("/join")
    assert "Pick a fruit" in r.text


def test_next_from_idle_activates_first(app_client):
    admin(app_client)
    r = app_client.post("/admin/next")
    assert r.status_code == 303
    r = app_client.get("/join")
    assert "Pick a fruit" in r.text


def test_next_past_last_ends_session(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q3"})  # last question
    app_client.post("/admin/next")
    r = app_client.get("/join")
    assert "Thanks, that's the last one" in r.text


def test_prev_walks_backwards(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q3"})
    app_client.post("/admin/prev")
    r = app_client.get("/join")
    assert "Why?" in r.text  # q2


def test_prev_on_first_is_noop(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    app_client.post("/admin/prev")
    r = app_client.get("/join")
    assert "Pick a fruit" in r.text


def test_clear_returns_to_idle(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    app_client.post("/admin/clear")
    r = app_client.get("/join")
    assert "Waiting for presenter" in r.text


def test_end_session(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    app_client.post("/admin/end")
    r = app_client.get("/join")
    assert "Thanks, that's the last one" in r.text


def test_activating_after_end_reopens(app_client):
    admin(app_client)
    app_client.post("/admin/end")
    app_client.post("/admin/activate", data={"qid": "q1"})
    r = app_client.get("/join")
    assert "Pick a fruit" in r.text


def test_admin_actions_require_admin_cookie(app_client):
    # No prior /admin?token=... so no cookie yet.
    r = app_client.post("/admin/activate", data={"qid": "q1"})
    assert r.status_code == 401


# --- answer submission --------------------------------------------------------


def test_answer_mc_records_response(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    r = app_client.post("/answer", data={"question_id": "q1", "answer": "A"})
    assert r.status_code == 303
    r = app_client.get("/join")
    # Submitted message should be present after a successful answer.
    assert "Your answer is recorded" in r.text


def test_answer_free_text_records_response(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q2"})
    app_client.post("/answer", data={"question_id": "q2", "answer": "because"})
    r = app_client.get("/join")
    assert "because" in r.text  # textarea pre-filled with prior answer


def test_answer_resubmit_replaces(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q2"})
    app_client.post("/answer", data={"question_id": "q2", "answer": "first"})
    app_client.post("/answer", data={"question_id": "q2", "answer": "second"})
    r = app_client.get("/join")
    assert "second" in r.text
    assert "first" not in r.text


def test_answer_invalid_mc_option_ignored(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    app_client.post("/answer", data={"question_id": "q1", "answer": "BOGUS"})
    r = app_client.get("/join")
    # No "your answer is recorded" because we silently dropped the bogus value.
    assert "Your answer is recorded" not in r.text


def test_answer_for_inactive_question_ignored(app_client):
    admin(app_client)
    # No active question -> /answer should be a no-op.
    r = app_client.post("/answer", data={"question_id": "q1", "answer": "A"})
    assert r.status_code == 303
    r = app_client.get("/join")
    assert "Waiting for presenter" in r.text
