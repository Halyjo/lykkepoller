import secrets
from pathlib import Path

import typer
import uvicorn

from . import app as app_module
from . import questions as questions_mod

cli = typer.Typer(no_args_is_help=True, help="Poller: minimal live polling for presentations.")


@cli.command()
def run(
    yaml_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
):
    """Start the polling app for a YAML question file."""
    data = questions_mod.load(yaml_path)
    session_id = _friendly_id()
    admin_token = _friendly_id()

    fastapi_app = app_module.create_app(
        title=data["title"],
        questions=data["questions"],
        session_id=session_id,
        admin_token=admin_token,
    )

    _print_urls(host, port, session_id, admin_token)

    # cloudflared connects from a Cloudflare IP not in any default trusted list.
    # proxy_headers=True + forwarded_allow_ips="*" makes Uvicorn honor the
    # X-Forwarded-Proto/Host headers it sends, so the app can detect the public URL.
    uvicorn.run(
        fastapi_app,
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )


def _friendly_id() -> str:
    adjectives = [
        "blue",
        "red",
        "green",
        "amber",
        "silver",
        "golden",
        "winter",
        "spring",
        "summer",
        "autumn",
        "quiet",
        "bright",
        "swift",
        "calm",
        "wild",
        "humble",
    ]
    animals = [
        "otter",
        "fox",
        "owl",
        "lynx",
        "raven",
        "wolf",
        "deer",
        "moth",
        "hawk",
        "heron",
        "bear",
        "salmon",
    ]
    a = secrets.choice(adjectives)
    n = secrets.choice(animals)
    return f"{a}-{n}-{secrets.randbelow(9000) + 1000}"


def _print_urls(host: str, port: int, session_id: str, admin_token: str) -> None:
    base = f"http://{host}:{port}"
    print()
    print(f"Session:       {session_id}")
    print(f"Local join:    {base}/join")
    print(f"Local admin:   {base}/admin?token={admin_token}")
    print(f"Present:       {base}/present")
    print(f"QR:            {base}/qr.png")
    print()


def main() -> None:
    cli()
