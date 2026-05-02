"""FastAPI app. One running app == one session."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

PACKAGE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app(
    *,
    title: str,
    questions: list[dict],
    session_id: str,
    admin_token: str,
) -> FastAPI:
    app = FastAPI(title="poller")

    app.state.title = title
    app.state.questions = questions
    app.state.session_id = session_id
    app.state.admin_token = admin_token

    app.mount(
        "/static",
        StaticFiles(directory=str(PACKAGE_DIR / "static")),
        name="static",
    )

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/join")

    @app.get("/join", response_class=HTMLResponse)
    async def join(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "participant.html",
            {
                "title": title,
                "phase": "idle",
                "active_question": None,
                "prior_answer": None,
            },
        )

    @app.get("/admin", response_class=HTMLResponse)
    async def admin(request: Request, token: str | None = None):
        if token != admin_token:
            raise HTTPException(
                status_code=401,
                detail="Open the admin URL printed in your terminal.",
            )
        join_url = str(request.base_url).rstrip("/") + "/join"
        return TEMPLATES.TemplateResponse(
            request,
            "admin.html",
            {
                "title": title,
                "questions": questions,
                "session_id": session_id,
                "state": {
                    "active_question_id": None,
                    "ended": False,
                    "reveal_free_text": False,
                },
                "join_url": join_url,
                "join_url_source": "request",
                "public_url_override": "",
                "connected_count": 0,
            },
        )

    @app.get("/present", response_class=HTMLResponse)
    async def present(request: Request):
        join_url = str(request.base_url).rstrip("/") + "/join"
        qr_url = str(request.base_url).rstrip("/") + "/qr.png"
        return TEMPLATES.TemplateResponse(
            request,
            "present.html",
            {
                "title": title,
                "phase": "idle",
                "active_question": None,
                "join_url": join_url,
                "qr_url": qr_url,
                "connected_count": 0,
                "answered_count": 0,
            },
        )

    return app
