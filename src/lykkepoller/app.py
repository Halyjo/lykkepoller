"""FastAPI app. One running app == one session, backed by one SQLite file."""

import secrets
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db as db_module
from . import exports as exports_mod
from . import qr as qr_mod
from . import quiz as quiz_mod

PACKAGE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def _one_based(index: int | None) -> int:
    """Question position for display ("3 / 12"). None (idle) shows as 1; the
    counter is hidden in that state anyway."""
    return 1 if index is None else index + 1


def create_app(*, db_path: Path) -> FastAPI:
    app = FastAPI(title="lykkepoller")

    # One shared sqlite connection for the life of the app. SQLite serializes
    # writes internally, and our write rate (presenter clicks, occasional answer
    # submits, ~1Hz heartbeat per participant) is far below the contention point.
    conn = db_module.connect(db_path)
    db_module.init_schema(conn)
    session = db_module.get_session(conn)
    if session is None:
        raise RuntimeError(f"No session row in {db_path}; create one before starting the app.")

    app.state.conn = conn
    app.state.db_path = db_path
    app.state.session_id = session["id"]
    app.state.admin_token = session["admin_token"]
    # Set by the cloudflared watcher in cli._start_cloudflared once the public
    # URL is parsed. Stays None when --no-tunnel or when cloudflared fails.
    app.state.tunnel_url = None
    # Where /qr.png drops its file copy, and the URL that copy encodes.
    # Next to the database, so one file per session and nothing to clean up
    # in the directory the presenter happened to start from.
    app.state.qr_file_path = db_path.with_suffix(".qr.png")
    app.state.qr_file_url = None

    app.mount(
        "/static",
        StaticFiles(directory=str(PACKAGE_DIR / "static")),
        name="static",
    )

    # --- helpers --------------------------------------------------------------

    def current_session() -> dict:
        s = db_module.get_session(conn)
        assert s is not None
        return s

    def current_state() -> dict:
        return db_module.get_state(conn, app.state.session_id)

    def phase_of(state: dict) -> str:
        """IDLE / ACTIVE / ENDED, as seen by participants, /present and the
        /admin badge."""
        if state["ended"]:
            return "ended"
        if state["active_question_id"]:
            return "active"
        return "idle"

    def require_admin(request: Request) -> None:
        if request.cookies.get("admin_token") != app.state.admin_token:
            raise HTTPException(
                status_code=401,
                detail="Open the admin URL printed in your terminal.",
            )

    def ensure_participant_id(request: Request) -> tuple[str, bool]:
        """Return (participant_id, is_new). Caller sets the cookie on the response if new."""
        pid = request.cookies.get("participant_id")
        if pid:
            return pid, False
        return secrets.token_urlsafe(12), True

    def set_participant_cookie(resp, pid: str) -> None:
        # 8h cookie -- long enough for any single lecture, short enough that
        # casual reuse on shared machines doesn't carry over.
        resp.set_cookie("participant_id", pid, httponly=True, samesite="lax", max_age=60 * 60 * 8)

    def set_admin_cookie(resp, request: Request, token: str) -> None:
        # `secure` only when the request itself is https (which it will be behind
        # cloudflared because forwarded headers are honored, see uvicorn config).
        resp.set_cookie(
            "admin_token",
            token,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            max_age=60 * 60 * 24,
        )

    def activate(sess: dict, qid: str) -> None:
        """Open a question. Free text opens with its result panel already
        revealed -- nothing is approved yet, so there is nothing to leak, and
        the presenter wants approved answers to appear as they tick them off.
        Multiple choice and rating open hidden, so the room cannot see which
        way the vote is going and follow it."""
        q = quiz_mod.find_question(sess["questions"], qid)
        is_free_text = q is not None and q["type"] == "free_text"
        db_module.set_active_question(conn, sess["id"], qid, reveal_free_text=is_free_text)

    def question_index(sess: dict, state: dict) -> int | None:
        """Where in the deck we are: the position of the active question, or
        None when idle."""
        qid = state["active_question_id"]
        if qid is None:
            return None
        for i, q in enumerate(sess["questions"]):
            if q["id"] == qid:
                return i
        return None

    def prev_index(current: int | None) -> int | None:
        """Step back one. None when nothing is active (no-op) or already first."""
        if current is None or current <= 0:
            return None
        return current - 1

    def next_index(questions: list[dict], current: int | None) -> int | None:
        """Step forward one. Returns None to mean 'past the last question --
        end the session'."""
        n = len(questions)
        if n == 0:
            return None
        if current is None:
            return 0
        if current + 1 < n:
            return current + 1
        return None

    def compute_base_url(request: Request) -> tuple[str, str]:
        """Return (base_url, source) for the public-facing URL.

        Priority:
          1. manual override stored on the session row (typed in /admin form)
          2. tunnel URL set by the cloudflared watcher (in-memory, this process)
          3. X-Forwarded-Host / X-Forwarded-Proto (set by cloudflared on its
             own traffic)
          4. request.base_url (Uvicorn already honors X-Forwarded-Proto when
             started with proxy_headers=True; the host comes from the Host header)

        `source` is one of "override" / "tunnel" / "headers" / "request" /
        "localhost". It is shown on /admin so the presenter can see where the
        join URL came from without guessing.
        """
        sess = db_module.get_session(conn)
        override = (sess or {}).get("public_url_override")
        if override:
            return override.rstrip("/"), "override"
        tunnel = getattr(app.state, "tunnel_url", None)
        if tunnel:
            return tunnel.rstrip("/"), "tunnel"
        fwd_host = request.headers.get("x-forwarded-host")
        if fwd_host:
            proto = request.headers.get("x-forwarded-proto") or request.url.scheme
            return f"{proto}://{fwd_host}".rstrip("/"), "headers"
        base = str(request.base_url).rstrip("/")
        if "127.0.0.1" in base or "localhost" in base:
            return base, "localhost"
        return base, "request"

    def _participant_status(active_q, session_id, pid):
        """Return (prior_unique_answer, free_text_submit_count) for the active
        question. (None, 0) if there is no active question.

        prior_unique_answer -- the participant's recorded answer for one-shot
                               question types (multiple_choice, rating).
                               String value (option id, or step number) or
                               None. Drives the "you answered: X" lock
                               (issue #5) on the participant page.
        free_text_count     -- count of this participant's free-text submissions
                               for the active question. >0 triggers the
                               "answer recorded -- submit another if you like"
                               hint (issue #6).
        """
        if active_q is None:
            return None, 0
        if active_q["type"] in ("multiple_choice", "rating"):
            return db_module.get_unique_answer(conn, session_id, active_q["id"], pid), 0
        return None, db_module.participant_text_count(conn, session_id, active_q["id"], pid)

    def compute_results(question: dict) -> dict:
        """Build the result summary dict used by both /admin and /present."""
        sid = app.state.session_id
        qid = question["id"]
        if question["type"] == "rating":
            counts = db_module.aggregate_choice_counts(conn, sid, qid)
            steps = question["steps"]
            buckets = []
            for step in range(1, steps + 1):
                buckets.append({"step": step, "count": counts.get(str(step), 0)})
            total = sum(b["count"] for b in buckets)
            for b in buckets:
                b["pct"] = round(100 * b["count"] / total) if total else 0
            # Average rating, rounded to one decimal. None when no responses
            # so the template can hide it cleanly.
            avg = (
                round(sum(b["step"] * b["count"] for b in buckets) / total, 2)
                if total
                else None
            )
            return {
                "type": "rating",
                "steps": steps,
                "low_label": question["low_label"],
                "high_label": question["high_label"],
                "buckets": buckets,
                "total": total,
                "average": avg,
            }
        if question["type"] == "multiple_choice":
            counts = db_module.aggregate_choice_counts(conn, sid, qid)
            opts = []
            any_correct = False
            for o in question.get("options", []):
                n = counts.get(o["id"], 0)
                is_correct = bool(o.get("is_correct", False))
                any_correct = any_correct or is_correct
                opts.append(
                    {"id": o["id"], "label": o["label"], "count": n, "is_correct": is_correct}
                )
            total = sum(o["count"] for o in opts)
            for o in opts:
                o["pct"] = round(100 * o["count"] / total) if total else 0
            return {
                "type": "multiple_choice",
                "options": opts,
                "total": total,
                "any_correct": any_correct,
            }
        else:
            rows = db_module.list_text_responses(conn, sid, qid)
            approved_ids = {a["id"] for a in db_module.list_approved_free_text(conn, sid, qid)}
            rejected_ids = db_module.list_rejected_ids(conn, sid, qid)
            answers = [
                {
                    "id": r["id"],
                    "answer": r["answer"],
                    "approved": r["id"] in approved_ids,
                    "rejected": r["id"] in rejected_ids,
                }
                for r in rows
            ]
            # Note: keys named "items"/"keys"/"values" collide with dict builtin
            # methods under Jinja2's attribute access (`res.items`), so we use
            # "answers" / "approved_answers" instead.
            return {
                "type": "free_text",
                "answers": answers,
                "approved_answers": [a for a in answers if a["approved"]],
                "total": len(answers),
            }

    # --- public routes --------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(f"/join/{app.state.session_id}")

    @app.get("/join", include_in_schema=False)
    async def join_compat():
        return RedirectResponse(f"/join/{app.state.session_id}", status_code=303)

    @app.get("/join/{session_id}", response_class=HTMLResponse)
    async def join(request: Request, session_id: str):
        if session_id != app.state.session_id:
            raise HTTPException(status_code=404)
        pid, is_new = ensure_participant_id(request)
        sess = current_session()
        state = current_state()
        active_q = (
            quiz_mod.find_question(sess["questions"], state["active_question_id"])
            if state["active_question_id"]
            else None
        )
        prior_mc, text_count = _participant_status(active_q, sess["id"], pid)
        resp = TEMPLATES.TemplateResponse(
            request,
            "participant.html",
            {
                "title": sess["title"],
                "phase": phase_of(state),
                "active_question": active_q,
                "prior_mc_answer": prior_mc,
                "participant_text_count": text_count,
                "session_id": sess["id"],
            },
        )
        if is_new:
            set_participant_cookie(resp, pid)
        return resp

    @app.post("/answer/{session_id}")
    async def answer_submit(
        request: Request,
        session_id: str,
        question_id: str = Form(...),
        answer: str = Form(...),
    ):
        if session_id != app.state.session_id:
            raise HTTPException(status_code=404)
        pid, is_new = ensure_participant_id(request)
        sess = current_session()
        state = current_state()

        # Submissions for the wrong (or no) active question are silently dropped.
        # The participant page polls and re-renders; a stale submit usually means
        # the presenter just moved on, and the participant page is about to update.
        if state["active_question_id"] == question_id:
            q = quiz_mod.find_question(sess["questions"], question_id)
            if q is not None:
                if q["type"] == "multiple_choice":
                    valid = {o["id"] for o in q.get("options", [])}
                    if answer in valid:
                        # Returns False if this participant already answered;
                        # we silently drop the resubmit (issue #5).
                        db_module.record_unique_answer(
                            conn, sess["id"], question_id, pid, answer
                        )
                elif q["type"] == "rating":
                    # Answer must be "1".."steps". Same one-shot semantics as MC.
                    try:
                        n = int(answer)
                    except ValueError:
                        n = 0
                    if 1 <= n <= q["steps"]:
                        db_module.record_unique_answer(
                            conn, sess["id"], question_id, pid, str(n)
                        )
                else:
                    text = answer.strip()
                    if text:
                        db_module.append_text_answer(
                            conn, sess["id"], question_id, pid, text
                        )

        resp = RedirectResponse(f"/join/{session_id}", status_code=303)
        if is_new:
            set_participant_cookie(resp, pid)
        return resp

    @app.get("/present", response_class=HTMLResponse)
    async def present(request: Request, token: str | None = None):
        # Same first-visit cookie flow as /admin: ?token=... validates and
        # 303s to a clean /present so the token doesn't sit in the URL bar
        # during screen share. Presenters can drive the session from /present
        # using the admin keyboard shortcuts once the cookie is set.
        if token is not None:
            if token != app.state.admin_token:
                raise HTTPException(status_code=401, detail="Bad admin token.")
            resp = RedirectResponse("/present", status_code=303)
            set_admin_cookie(resp, request, token)
            return resp

        sess = current_session()
        state = current_state()
        active_q = (
            quiz_mod.find_question(sess["questions"], state["active_question_id"])
            if state["active_question_id"]
            else None
        )
        active_results = compute_results(active_q) if active_q else None
        base, _ = compute_base_url(request)
        join_url = base + f"/join/{sess['id']}"
        qr_url = base + "/qr.png"
        is_admin = request.cookies.get("admin_token") == app.state.admin_token
        return TEMPLATES.TemplateResponse(
            request,
            "present.html",
            {
                "title": sess["title"],
                "phase": phase_of(state),
                "active_question": active_q,
                "active_results": active_results,
                "question_number": _one_based(question_index(sess, state)),
                "question_count": len(sess["questions"]),
                "reveal_free_text": state["reveal_free_text"],
                "reveal_correct": state["reveal_correct"],
                "join_url": join_url,
                "qr_url": qr_url,
                "is_admin": is_admin,
                "connected_count": db_module.count_connected(conn, sess["id"]),
                "answered_count": (
                    db_module.count_answered(conn, sess["id"], state["active_question_id"])
                    if state["active_question_id"]
                    else 0
                ),
                "theme": sess["theme"],
            },
        )

    # --- admin GET (cookie redirect) -----------------------------------------

    @app.get("/admin", response_class=HTMLResponse)
    async def admin(request: Request, token: str | None = None):
        # First-visit flow: ?token=... validates, sets cookie, redirects to /admin
        # so the token disappears from the address bar (avoids leaking on screen
        # share). Subsequent requests are authed by cookie only.
        if token is not None:
            if token != app.state.admin_token:
                raise HTTPException(status_code=401, detail="Bad admin token.")
            resp = RedirectResponse("/admin", status_code=303)
            set_admin_cookie(resp, request, token)
            return resp
        require_admin(request)

        sess = current_session()
        state = current_state()
        base, base_src = compute_base_url(request)
        join_url = base + f"/join/{sess['id']}"
        results = {q["id"]: compute_results(q) for q in sess["questions"]}
        active_id = state["active_question_id"]
        return TEMPLATES.TemplateResponse(
            request,
            "admin.html",
            {
                "title": sess["title"],
                "questions": sess["questions"],
                "session_id": sess["id"],
                "state": state,
                "join_url": join_url,
                "join_url_source": base_src,
                "public_url_override": sess.get("public_url_override") or "",
                "connected_count": db_module.count_connected(conn, sess["id"]),
                "results": results,
                "answered_count": (
                    db_module.count_answered(conn, sess["id"], active_id) if active_id else 0
                ),
            },
        )

    # --- admin actions --------------------------------------------------------

    @app.post("/admin/activate")
    async def admin_activate(request: Request, qid: str = Form(...)):
        require_admin(request)
        sess = current_session()
        if quiz_mod.find_question(sess["questions"], qid) is not None:
            activate(sess, qid)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/clear")
    async def admin_clear(request: Request):
        require_admin(request)
        db_module.clear_active_question(conn, app.state.session_id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/end")
    async def admin_end(request: Request):
        require_admin(request)
        db_module.end_session(conn, app.state.session_id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/next")
    async def admin_next(request: Request):
        require_admin(request)
        sess = current_session()
        state = current_state()
        target = next_index(sess["questions"], question_index(sess, state))
        if target is None:
            # Past the last question -- end the session.
            db_module.end_session(conn, sess["id"])
        else:
            activate(sess, sess["questions"][target]["id"])
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/prev")
    async def admin_prev(request: Request):
        require_admin(request)
        sess = current_session()
        state = current_state()
        target = prev_index(question_index(sess, state))
        if target is not None:
            activate(sess, sess["questions"][target]["id"])
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/reveal")
    async def admin_reveal(request: Request, on: str = Form(...)):
        require_admin(request)
        db_module.set_reveal_free_text(conn, app.state.session_id, on == "1")
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/reveal_correct")
    async def admin_reveal_correct(request: Request, on: str = Form(...)):
        # Toggled per-question via the C shortcut. set_active_question resets
        # this back to 0, so each new question starts hidden.
        require_admin(request)
        db_module.set_reveal_correct(conn, app.state.session_id, on == "1")
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/approve")
    async def admin_approve(
        request: Request,
        qid: str = Form(...),
        rid: int = Form(...),
        approved: str = Form(...),
    ):
        require_admin(request)
        if approved == "1":
            db_module.approve_free_text(conn, app.state.session_id, qid, rid)
        else:
            db_module.unapprove_free_text(conn, app.state.session_id, qid, rid)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/reject")
    async def admin_reject(
        request: Request,
        qid: str = Form(...),
        rid: int = Form(...),
        rejected: str = Form(...),
    ):
        require_admin(request)
        if rejected == "1":
            db_module.reject_free_text(conn, app.state.session_id, qid, rid)
        else:
            db_module.unreject_free_text(conn, app.state.session_id, qid, rid)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/approve_all")
    async def admin_approve_all(request: Request):
        # Bulk-approve every response for the active free-text question. The
        # presenter typically uses this to skim new answers, glance for
        # anything off, and accept the rest in one click. No-op when there is
        # no active question or the active question is not free-text.
        require_admin(request)
        state = current_state()
        qid = state["active_question_id"]
        if qid:
            sess = current_session()
            q = quiz_mod.find_question(sess["questions"], qid)
            if q is not None and q["type"] == "free_text":
                db_module.approve_all_existing_free_text(conn, sess["id"], qid)
        return RedirectResponse("/admin", status_code=303)

    @app.get("/admin/export.csv")
    async def admin_export(request: Request):
        require_admin(request)
        sess = current_session()
        csv_text = exports_mod.csv_for_session(conn, sess)
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{sess["id"]}.csv"',
            },
        )

    @app.post("/admin/override")
    async def admin_override(request: Request, url: str = Form("")):
        require_admin(request)
        db_module.set_public_url_override(conn, app.state.session_id, url.strip())
        return RedirectResponse("/admin", status_code=303)

    # --- QR --------------------------------------------------------------------

    @app.get("/qr.png")
    async def qr_image(request: Request):
        # The QR encodes the inferred public join URL. With cloudflared, this
        # picks up the tunnel URL automatically (via X-Forwarded-Host); in pure
        # local dev it falls back to http://127.0.0.1:port/join.
        base, _ = compute_base_url(request)
        join_url = base + f"/join/{app.state.session_id}"
        png = qr_mod.png_bytes(join_url)
        # Also drop a copy next to the session database so the presenter can
        # paste the QR into a slide or a chat message. Written only when the
        # URL changes: this endpoint is hit by every phone and by /present,
        # and rewriting the same file a few times a second is disk churn.
        if app.state.qr_file_url != join_url:
            qr_mod.png_local(join_url, path=str(app.state.qr_file_path))
            app.state.qr_file_url = join_url
        return Response(content=png, media_type="image/png")

    # --- live polling JSON -----------------------------------------------------

    @app.get("/api/participant/state/{session_id}")
    async def api_participant_state(request: Request, session_id: str):
        if session_id != app.state.session_id:
            raise HTTPException(status_code=404)
        # The participant page polls this endpoint every ~1.5s. It doubles as
        # the heartbeat -- every poll updates participants.last_seen_at, which
        # drives the "connected" count on /admin and /present.
        pid, is_new = ensure_participant_id(request)
        sess = current_session()
        state = current_state()
        db_module.heartbeat(conn, sess["id"], pid)
        active_q = (
            quiz_mod.find_question(sess["questions"], state["active_question_id"])
            if state["active_question_id"]
            else None
        )
        prior_mc, text_count = _participant_status(active_q, sess["id"], pid)
        payload = {
            "phase": phase_of(state),
            "active_question": active_q,
            "prior_mc_answer": prior_mc,
            "participant_text_count": text_count,
        }
        resp = JSONResponse(payload)
        if is_new:
            set_participant_cookie(resp, pid)
        return resp

    @app.get("/api/admin/state")
    async def api_admin_state(request: Request):
        require_admin(request)
        sess = current_session()
        state = current_state()
        active_id = state["active_question_id"]
        return {
            "phase": phase_of(state),
            "active_question_id": active_id,
            "ended": state["ended"],
            "reveal_free_text": state["reveal_free_text"],
            "reveal_correct": state["reveal_correct"],
            "connected_count": db_module.count_connected(conn, sess["id"]),
            "answered_count": (
                db_module.count_answered(conn, sess["id"], active_id) if active_id else 0
            ),
            "results": {q["id"]: compute_results(q) for q in sess["questions"]},
        }

    @app.get("/api/present/state")
    async def api_present_state(request: Request):
        sess = current_session()
        state = current_state()
        active_id = state["active_question_id"]
        active_q = quiz_mod.find_question(sess["questions"], active_id) if active_id else None
        return {
            "phase": phase_of(state),
            "active_question": active_q,
            "active_results": compute_results(active_q) if active_q else None,
            "reveal_free_text": state["reveal_free_text"],
            "reveal_correct": state["reveal_correct"],
            "connected_count": db_module.count_connected(conn, sess["id"]),
            "answered_count": (
                db_module.count_answered(conn, sess["id"], active_id) if active_id else 0
            ),
        }

    return app
