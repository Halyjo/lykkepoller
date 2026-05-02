"""FastAPI app. One running app == one session, backed by one SQLite file."""

import secrets
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db as db_module
from . import questions as questions_mod

PACKAGE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app(*, db_path: Path) -> FastAPI:
    app = FastAPI(title="poller")

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

    def prev_qid(questions: list[dict], current: str | None) -> str | None:
        if current is None:
            return None
        ids = [q["id"] for q in questions]
        if current not in ids:
            return None
        i = ids.index(current)
        return ids[i - 1] if i > 0 else None

    def next_qid(questions: list[dict], current: str | None) -> str | None:
        """Returns None to mean 'past the last question -- end the session'."""
        ids = [q["id"] for q in questions]
        if not ids:
            return None
        if current is None or current not in ids:
            return ids[0]
        i = ids.index(current)
        return ids[i + 1] if i + 1 < len(ids) else None

    # --- public routes --------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/join")

    @app.get("/join", response_class=HTMLResponse)
    async def join(request: Request):
        pid, is_new = ensure_participant_id(request)
        sess = current_session()
        state = current_state()
        active_q = (
            questions_mod.find_question(sess["questions"], state["active_question_id"])
            if state["active_question_id"]
            else None
        )
        prior = (
            db_module.get_response(conn, sess["id"], state["active_question_id"], pid)
            if state["active_question_id"]
            else None
        )
        resp = TEMPLATES.TemplateResponse(
            request,
            "participant.html",
            {
                "title": sess["title"],
                "phase": phase_of(state),
                "active_question": active_q,
                "prior_answer": prior,
            },
        )
        if is_new:
            set_participant_cookie(resp, pid)
        return resp

    @app.post("/answer")
    async def answer_submit(
        request: Request,
        question_id: str = Form(...),
        answer: str = Form(...),
    ):
        pid, is_new = ensure_participant_id(request)
        sess = current_session()
        state = current_state()

        # Submissions for the wrong (or no) active question are silently dropped.
        # The participant page polls and re-renders; a stale submit usually means
        # the presenter just moved on, and the participant page is about to update.
        if state["active_question_id"] == question_id:
            q = questions_mod.find_question(sess["questions"], question_id)
            if q is not None:
                if q["type"] == "multiple_choice":
                    valid = {o["id"] for o in q.get("options", [])}
                    if answer in valid:
                        db_module.insert_response(conn, sess["id"], question_id, pid, answer)
                else:
                    text = answer.strip()
                    if text:
                        db_module.insert_response(conn, sess["id"], question_id, pid, text)

        resp = RedirectResponse("/join", status_code=303)
        if is_new:
            set_participant_cookie(resp, pid)
        return resp

    @app.get("/present", response_class=HTMLResponse)
    async def present(request: Request):
        sess = current_session()
        state = current_state()
        active_q = (
            questions_mod.find_question(sess["questions"], state["active_question_id"])
            if state["active_question_id"]
            else None
        )
        join_url = str(request.base_url).rstrip("/") + "/join"
        qr_url = str(request.base_url).rstrip("/") + "/qr.png"
        return TEMPLATES.TemplateResponse(
            request,
            "present.html",
            {
                "title": sess["title"],
                "phase": phase_of(state),
                "active_question": active_q,
                "join_url": join_url,
                "qr_url": qr_url,
                "connected_count": db_module.count_connected(conn, sess["id"]),
                "answered_count": (
                    db_module.count_answered(conn, sess["id"], state["active_question_id"])
                    if state["active_question_id"]
                    else 0
                ),
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
        join_url = str(request.base_url).rstrip("/") + "/join"
        return TEMPLATES.TemplateResponse(
            request,
            "admin.html",
            {
                "title": sess["title"],
                "questions": sess["questions"],
                "session_id": sess["id"],
                "state": state,
                "join_url": join_url,
                "join_url_source": "request",
                "public_url_override": sess.get("public_url_override") or "",
                "connected_count": db_module.count_connected(conn, sess["id"]),
            },
        )

    # --- admin actions --------------------------------------------------------

    @app.post("/admin/activate")
    async def admin_activate(request: Request, qid: str = Form(...)):
        require_admin(request)
        sess = current_session()
        if questions_mod.find_question(sess["questions"], qid) is not None:
            db_module.set_active_question(conn, sess["id"], qid)
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
        target = next_qid(sess["questions"], state["active_question_id"])
        if target is None:
            # Past the last question -- end the session.
            db_module.end_session(conn, sess["id"])
        else:
            db_module.set_active_question(conn, sess["id"], target)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/prev")
    async def admin_prev(request: Request):
        require_admin(request)
        sess = current_session()
        state = current_state()
        target = prev_qid(sess["questions"], state["active_question_id"])
        if target is not None:
            db_module.set_active_question(conn, sess["id"], target)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/reveal")
    async def admin_reveal(request: Request, on: str = Form(...)):
        require_admin(request)
        db_module.set_reveal_free_text(conn, app.state.session_id, on == "1")
        return RedirectResponse("/admin", status_code=303)

    return app
