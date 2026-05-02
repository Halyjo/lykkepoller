"""FastAPI app. One running app == one session, backed by one SQLite file."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
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

    def current_session() -> dict:
        # Re-read on each request so changes (e.g. public_url_override) are visible.
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

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/join")

    @app.get("/join", response_class=HTMLResponse)
    async def join(request: Request):
        sess = current_session()
        state = current_state()
        active_q = (
            questions_mod.find_question(sess["questions"], state["active_question_id"])
            if state["active_question_id"]
            else None
        )
        return TEMPLATES.TemplateResponse(
            request,
            "participant.html",
            {
                "title": sess["title"],
                "phase": phase_of(state),
                "active_question": active_q,
                "prior_answer": None,
            },
        )

    @app.get("/admin", response_class=HTMLResponse)
    async def admin(request: Request, token: str | None = None):
        if token != app.state.admin_token:
            raise HTTPException(
                status_code=401,
                detail="Open the admin URL printed in your terminal.",
            )
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

    return app
