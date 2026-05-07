"""HTTP-level integration tests using FastAPI's TestClient."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lykkepoller import app as app_module
from lykkepoller import db


def make_qs():
    return [
        {
            "id": "q1",
            "type": "multiple_choice",
            "prompt": "Pick a fruit",
            "options": [
                {"id": "A", "label": "Apple"},
                {"id": "B", "label": "Banana", "is_correct": True},
            ],
        },
        {"id": "q2", "type": "free_text", "prompt": "Why?"},
        {
            "id": "q3",
            "type": "multiple_choice",
            "prompt": "Color",
            "options": [{"id": "r", "label": "red"}, {"id": "b", "label": "blue"}],
        },
        {
            "id": "qr",
            "type": "rating_scale",
            "prompt": "How was it?",
            "steps": 5,
            "low_label": "Bad",
            "high_label": "Good",
        },
    ]


SID = "blue-otter-1234"


def submit(client, qid, answer, participant_cookie=None):
    if participant_cookie:
        client.cookies.set("participant_id", participant_cookie)
    return client.post(f"/answer/{SID}", data={"question_id": qid, "answer": answer})


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
    # Issue #5: after submitting, the form is replaced with a locked
    # "You answered: ..." block so the participant cannot re-pick.
    assert "You answered" in r.text
    assert "Apple" in r.text  # the chosen option's label is shown
    assert 'name="answer"' not in r.text  # the form is gone


def test_mc_resubmit_does_not_change_answer(app_client):
    """Issue #5: even if a malicious / impatient participant POSTs again,
    their first answer stands."""
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    app_client.post("/answer", data={"question_id": "q1", "answer": "A"})
    app_client.post("/answer", data={"question_id": "q1", "answer": "B"})
    r = app_client.get("/api/admin/state").json()
    by_id = {o["id"]: o for o in r["results"]["q1"]["options"]}
    assert by_id["A"]["count"] == 1
    assert by_id["B"]["count"] == 0


def test_answer_free_text_records_response(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q2"})
    app_client.post("/answer", data={"question_id": "q2", "answer": "because"})
    r = app_client.get("/join")
    # Issue #6: the textarea is NOT pre-filled (so the participant can write
    # a new answer easily), but a hint confirms the submission landed.
    assert "Your answer is registered" in r.text
    # The textarea should be empty (no value attribute / no inner text).
    assert ">because</textarea>" not in r.text


def test_answer_free_text_allows_multiple_submits(app_client):
    """Issue #6: free text is append-only -- submitting twice keeps both."""
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q2"})
    app_client.post("/answer", data={"question_id": "q2", "answer": "first"})
    app_client.post("/answer", data={"question_id": "q2", "answer": "second"})
    # Both answers should be visible to the admin.
    r = app_client.get("/admin")
    assert "first" in r.text
    assert "second" in r.text
    # Participant page hint reflects the count.
    r = app_client.get("/join")
    assert "2 answers registered" in r.text


def test_answer_invalid_mc_option_ignored(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    app_client.post("/answer", data={"question_id": "q1", "answer": "BOGUS"})
    r = app_client.get("/join")
    # No "you answered" lock because we silently dropped the bogus value;
    # the form is still rendered and ready for a real answer.
    assert "You answered" not in r.text
    assert 'name="answer"' in r.text


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
    from lykkepoller import db as dbm

    c = dbm.connect(app_client.app.state.db_path)
    rows = dbm.list_text_responses(c, "blue-otter-1234", "q2")
    rid = next(r2["id"] for r2 in rows if r2["answer"] == "first answer")
    c.close()
    app_client.post("/admin/approve", data={"qid": "q2", "rid": rid, "approved": "1"})
    r = app_client.get("/admin")
    assert "Unapprove" in r.text  # button text flips after approval


def test_present_mc_results_hidden_until_revealed(app_client):
    """MC bars on /present are hidden until the presenter presses R or C.
    The total count is always shown so the audience knows responses are
    coming in."""
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    submit_answer(app_client, "q1", "A", "p-alice")
    r = app_client.get("/present")
    assert "1 response received" in r.text
    assert 'class="bar' not in r.text  # bars hidden
    # After R, bars appear.
    app_client.post("/admin/reveal", data={"on": "1"})
    r = app_client.get("/present")
    assert "Apple" in r.text
    assert "1 (100%)" in r.text


def test_present_show_correct_alone_reveals_bars(app_client):
    """Pressing C (Show correct) on its own should be enough to reveal the
    bar chart -- otherwise the highlight has nothing to land on. This was
    the 'reveal still does not work' bug."""
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    submit_answer(app_client, "q1", "B", "p-alice")  # the correct option
    # Note: not pressing R. Just C.
    app_client.post("/admin/reveal_correct", data={"on": "1"})
    r = app_client.get("/present")
    assert "Apple" in r.text
    assert "Banana" in r.text
    assert 'class="bar correct"' in r.text
    assert 'class="bar dimmed"' in r.text


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

    from lykkepoller import db as dbm

    c = dbm.connect(app_client.app.state.db_path)
    rows = dbm.list_text_responses(c, "blue-otter-1234", "q2")
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


# --- live polling, QR, public URL inference (M5) -----------------------------


def test_qr_png_is_an_image(app_client):
    r = app_client.get("/qr.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    # PNG magic header
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_api_participant_state_returns_phase_and_heartbeats(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    # Ensure no participant cookie carried over from admin actions.
    app_client.cookies.delete("participant_id")
    r = app_client.get("/api/participant/state")
    assert r.status_code == 200
    payload = r.json()
    assert payload["phase"] == "active"
    assert payload["active_question"]["id"] == "q1"
    # The poll set a participant cookie; subsequent polls should reuse it.
    assert "participant_id" in app_client.cookies
    # The poll should have heartbeated -> connected_count >= 1 on /admin.
    r = app_client.get("/api/admin/state")
    assert r.json()["connected_count"] >= 1


def test_api_admin_state_returns_results(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    submit_answer(app_client, "q1", "A", "p-alice")
    submit_answer(app_client, "q1", "B", "p-bob")
    r = app_client.get("/api/admin/state")
    assert r.status_code == 200
    p = r.json()
    assert p["phase"] == "active"
    assert p["active_question_id"] == "q1"
    assert p["results"]["q1"]["type"] == "multiple_choice"
    assert p["results"]["q1"]["total"] == 2


def test_api_present_state_includes_active_results(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q2"})
    submit_answer(app_client, "q2", "first", "p-alice")
    r = app_client.get("/api/present/state")
    p = r.json()
    assert p["phase"] == "active"
    assert p["active_question"]["id"] == "q2"
    assert p["active_results"]["total"] == 1
    assert p["reveal_free_text"] is False


def test_admin_override_sets_public_url(app_client):
    admin(app_client)
    r = app_client.post("/admin/override", data={"url": "https://abc.example"})
    assert r.status_code == 303
    r = app_client.get("/admin")
    assert "https://abc.example" in r.text
    assert "source: override" in r.text


def test_admin_override_clear(app_client):
    admin(app_client)
    app_client.post("/admin/override", data={"url": "https://abc.example"})
    app_client.post("/admin/override", data={"url": ""})
    r = app_client.get("/admin")
    assert "source: override" not in r.text


def test_join_url_uses_forwarded_host(app_client):
    admin(app_client)
    r = app_client.get(
        "/admin",
        headers={
            "x-forwarded-host": "tunnel.cfargotunnel.com",
            "x-forwarded-proto": "https",
        },
    )
    assert "https://tunnel.cfargotunnel.com/join" in r.text
    assert "source: headers" in r.text


def test_tunnel_url_used_when_no_override(app_client):
    # Simulates what cli._start_cloudflared does after parsing the URL out of
    # cloudflared's stderr: it sets app.state.tunnel_url. compute_base_url
    # should pick that up for local requests (no x-forwarded-host).
    admin(app_client)
    app_client.app.state.tunnel_url = "https://abc-def-ghi.trycloudflare.com"
    r = app_client.get("/admin")
    assert "https://abc-def-ghi.trycloudflare.com/join" in r.text
    assert "source: tunnel" in r.text


def test_manual_override_beats_tunnel_url(app_client):
    # Manual override (typed in /admin form) wins over the auto tunnel URL.
    admin(app_client)
    app_client.app.state.tunnel_url = "https://abc-def-ghi.trycloudflare.com"
    app_client.post("/admin/override", data={"url": "https://my.example"})
    r = app_client.get("/admin")
    assert "https://my.example/join" in r.text
    assert "source: override" in r.text


# --- correct-answer reveal --------------------------------------------------


def test_results_include_is_correct(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    r = app_client.get("/api/admin/state").json()
    opts = r["results"]["q1"]["options"]
    by_id = {o["id"]: o for o in opts}
    assert by_id["A"]["is_correct"] is False
    assert by_id["B"]["is_correct"] is True
    assert r["results"]["q1"]["any_correct"] is True
    # q3 has no is_correct anywhere
    assert r["results"]["q3"]["any_correct"] is False


def test_reveal_correct_endpoint(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    assert app_client.get("/api/admin/state").json()["reveal_correct"] is False
    app_client.post("/admin/reveal_correct", data={"on": "1"})
    assert app_client.get("/api/admin/state").json()["reveal_correct"] is True
    app_client.post("/admin/reveal_correct", data={"on": "0"})
    assert app_client.get("/api/admin/state").json()["reveal_correct"] is False


def test_advancing_to_next_question_resets_reveal_correct(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    app_client.post("/admin/reveal_correct", data={"on": "1"})
    assert app_client.get("/api/admin/state").json()["reveal_correct"] is True
    # Pressing Next (or activating any other question) should reset to off.
    app_client.post("/admin/next")
    assert app_client.get("/api/admin/state").json()["reveal_correct"] is False


def test_admin_renders_correct_class_when_revealed(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    submit_answer(app_client, "q1", "A", "p-alice")  # the wrong answer
    submit_answer(app_client, "q1", "B", "p-bob")  # the right answer
    app_client.post("/admin/reveal_correct", data={"on": "1"})
    r = app_client.get("/admin")
    assert 'class="bar correct"' in r.text
    assert 'class="bar dimmed"' in r.text


def test_present_renders_correct_class_when_revealed(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})
    app_client.post("/admin/reveal_correct", data={"on": "1"})
    r = app_client.get("/present")
    assert "correct" in r.text
    assert "dimmed" in r.text


# --- DB filename + source filename ------------------------------------------


def test_admin_approve_all_bulk_approves(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q2"})
    submit_answer(app_client, "q2", "first", "p-alice")
    submit_answer(app_client, "q2", "second", "p-bob")
    submit_answer(app_client, "q2", "third", "p-carol")
    r = app_client.post("/admin/approve_all")
    assert r.status_code == 303
    # All three should now appear with the Unapprove button.
    page = app_client.get("/admin").text
    assert page.count("Unapprove") >= 3
    # And /present (with reveal on) shows all three.
    app_client.post("/admin/reveal", data={"on": "1"})
    pres = app_client.get("/present").text
    for txt in ("first", "second", "third"):
        assert txt in pres


def test_admin_approve_all_idempotent_picks_up_new(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q2"})
    submit_answer(app_client, "q2", "first", "p-alice")
    app_client.post("/admin/approve_all")
    # New answer arrives; A again accepts it without unapproving the old one.
    submit_answer(app_client, "q2", "second", "p-bob")
    app_client.post("/admin/approve_all")
    page = app_client.get("/admin").text
    assert page.count("Unapprove") >= 2


def test_admin_approve_all_noop_for_mc(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "q1"})  # multiple_choice
    submit_answer(app_client, "q1", "A", "p-alice")
    r = app_client.post("/admin/approve_all")
    assert r.status_code == 303
    # Nothing should have ended up in approved_free_text.
    from lykkepoller import db as dbm

    c = dbm.connect(app_client.app.state.db_path)
    assert dbm.list_approved_free_text(c, "blue-otter-1234", "q1") == []
    c.close()


def test_admin_approve_all_requires_admin(app_client):
    r = app_client.post("/admin/approve_all")
    assert r.status_code == 401


def test_session_records_source_yaml_filename(app_client, tmp_path):
    # Reopen the DB directly to verify the column was populated by the
    # fixture's create_session() call (which intentionally does not pass one,
    # so the field is None here -- this just shows the field exists).
    from lykkepoller import db as dbm

    c = dbm.connect(app_client.app.state.db_path)
    s = dbm.get_session(c)
    assert "source_yaml_filename" in s
    c.close()


# --- rating_scale (issue #9) -------------------------------------------------


def test_rating_scale_renders_buttons_on_join(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "qr"})
    r = app_client.get(f"/join/{SID}")
    assert r.status_code == 200
    assert "How was it?" in r.text
    # End labels are anchored on the form
    assert "Bad" in r.text
    assert "Good" in r.text
    # 5 step buttons (values 1..5)
    for n in range(1, 6):
        assert f'value="{n}"' in r.text


def test_rating_scale_records_response(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "qr"})
    r = submit(app_client, "qr", "4", "p-alice")
    assert r.status_code == 303
    state = app_client.get("/api/admin/state").json()
    res = state["results"]["qr"]
    assert res["type"] == "rating_scale"
    assert res["total"] == 1
    by_step = {b["step"]: b for b in res["buckets"]}
    assert by_step[4]["count"] == 1
    assert res["average"] == 4.0


def test_rating_scale_is_one_shot_lock(app_client):
    """Like MC, a second submit is silently dropped (issue #5 semantics)."""
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "qr"})
    submit(app_client, "qr", "2", "p-alice")
    submit(app_client, "qr", "5", "p-alice")
    res = app_client.get("/api/admin/state").json()["results"]["qr"]
    by_step = {b["step"]: b["count"] for b in res["buckets"]}
    assert by_step[2] == 1
    assert by_step[5] == 0
    # Participant page should now show the locked summary, not the form.
    app_client.cookies.set("participant_id", "p-alice")
    page = app_client.get(f"/join/{SID}").text
    assert "Your answer is locked" in page
    assert "You answered" in page


def test_rating_scale_rejects_out_of_range(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "qr"})
    submit(app_client, "qr", "0", "p-alice")
    submit(app_client, "qr", "6", "p-bob")
    submit(app_client, "qr", "junk", "p-carol")
    res = app_client.get("/api/admin/state").json()["results"]["qr"]
    assert res["total"] == 0


def test_present_rating_hidden_until_revealed(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "qr"})
    submit(app_client, "qr", "3", "p-alice")
    submit(app_client, "qr", "5", "p-bob")
    page = app_client.get("/present").text
    assert "2 responses received" in page
    # No histogram bar markup until reveal toggle is on.
    assert 'class="bar"' not in page
    # average not leaked either
    assert "avg" not in page
    app_client.post("/admin/reveal", data={"on": "1"})
    page = app_client.get("/present").text
    assert "Bad" in page  # end label
    assert "Good" in page
    assert "avg 4" in page


def test_rating_scale_average_computed(app_client):
    admin(app_client)
    app_client.post("/admin/activate", data={"qid": "qr"})
    submit(app_client, "qr", "1", "p-a")
    submit(app_client, "qr", "5", "p-b")
    submit(app_client, "qr", "3", "p-c")
    res = app_client.get("/api/admin/state").json()["results"]["qr"]
    assert res["average"] == 3.0
