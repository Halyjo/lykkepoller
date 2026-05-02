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


# --- results, moderation, CSV (M4) -------------------------------------------


def submit_answer(client, qid, answer, participant_cookie=None):
    """POST /answer with a specific participant_id cookie so we can simulate
    multiple distinct participants from one TestClient."""
    if participant_cookie:
        client.cookies.set("participant_id", participant_cookie)
    return client.post("/answer", data={"question_id": qid, "answer": answer})


def test_admin_renders_mc_bars(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    submit_answer(app_client, "q1", "A", "p-alice")
    submit_answer(app_client, "q1", "B", "p-bob")
    submit_answer(app_client, "q1", "A", "p-carol")
    r = app_client.get("/admin")
    # Expect counts visible: A=2, B=1
    assert "Apple" in r.text
    assert "Banana" in r.text
    assert "2 · 67%" in r.text or "2 · 66%" in r.text
    assert "3 responses" in r.text


def test_admin_free_text_approve_toggle(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q2"})
    submit_answer(app_client, "q2", "first answer", "p-alice")
    submit_answer(app_client, "q2", "second answer", "p-bob")
    r = app_client.get("/admin")
    assert "first answer" in r.text
    assert "second answer" in r.text
    # Find the response id of the first answer for the approve POST.
    from poller import db as dbm

    c = dbm.connect(app_client.app.state.db_path)
    rows = dbm.list_responses(c, "blue-otter-1234", "q2")
    rid = next(r2["id"] for r2 in rows if r2["answer"] == "first answer")
    c.close()
    app_client.post("/admin/approve", data={"qid": "q2", "rid": rid, "approved": "1"})
    r = app_client.get("/admin")
    assert "Unapprove" in r.text  # button text flips after approval


def test_present_mc_results_visible(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    submit_answer(app_client, "q1", "A", "p-alice")
    r = app_client.get("/present")
    assert "Apple" in r.text
    assert "1 (100%)" in r.text


def test_present_free_text_shows_count_only_by_default(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q2"})
    submit_answer(app_client, "q2", "secret thought", "p-alice")
    r = app_client.get("/present")
    assert "1 response" in r.text
    assert "secret thought" not in r.text


def test_present_shows_only_approved_when_revealed(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q2"})
    submit_answer(app_client, "q2", "ok answer", "p-alice")
    submit_answer(app_client, "q2", "off-color answer", "p-bob")

    from poller import db as dbm

    c = dbm.connect(app_client.app.state.db_path)
    rows = dbm.list_responses(c, "blue-otter-1234", "q2")
    rid_ok = next(r["id"] for r in rows if r["answer"] == "ok answer")
    c.close()
    app_client.post("/admin/approve", data={"qid": "q2", "rid": rid_ok, "approved": "1"})
    # Reveal toggle ON.
    app_client.post("/admin/reveal", data={"on": "1"})
    r = app_client.get("/present")
    assert "ok answer" in r.text
    assert "off-color answer" not in r.text  # never shown until approved


def test_export_csv_endpoint(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    submit_answer(app_client, "q1", "A", "p-alice")
    r = app_client.get("/admin/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "session_id,question_id,question_type,prompt" in r.text
    assert "p-alice" in r.text
    assert "Apple" in r.text


def test_export_csv_requires_admin(app_client):
    r = app_client.get("/admin/export.csv")
    assert r.status_code == 401
