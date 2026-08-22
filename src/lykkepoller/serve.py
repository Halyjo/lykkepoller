"""Making a session file and putting it on the network.

Separate from cli.py so quiz.run() need not import the command line tool.
New sessions come from a quiz script, reopened ones from `lykkepoller run
--db`; both end up in serve().
"""

from __future__ import annotations

import atexit
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

import typer
import uvicorn

from . import app as app_module
from . import db as db_module

DATA_DIR = Path("data")

ADJECTIVES = ("blue red green amber silver golden winter spring summer autumn "
              "quiet bright swift calm wild humble").split()
ANIMALS = "otter fox owl lynx raven wolf deer moth hawk heron bear salmon".split()


def friendly_id() -> str:
    a, n = secrets.choice(ADJECTIVES), secrets.choice(ANIMALS)
    return f"{a}-{n}-{secrets.randbelow(9000) + 1000}"


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9._-]+", "-", text)).strip("-_.") or "session"


def new_session_db(*, title: str, questions: list[dict], theme: str, source_name: str) -> Path:
    """Create the session database and return its path.

    The name carries the date and the quiz file, so `ls data/` explains
    itself later; the random id keeps two runs of the same quiz apart.
    """
    session_id = friendly_id()
    name = f"{time.strftime('%Y-%m-%d')}-{_slug(Path(source_name).stem)}-{session_id}.sqlite"
    db_path = DATA_DIR / name
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        raise SystemExit(f"{db_path} exists. Reopen it: lykkepoller run --db {db_path}")

    conn = db_module.connect(db_path)
    db_module.init_schema(conn)
    db_module.create_session(
        conn, session_id, title, questions, friendly_id(),
        source_filename=source_name, theme=theme,
    )
    conn.close()
    return db_path


def serve(*, db_path: Path, host="127.0.0.1", port=8000, tunnel=True,
          domain=None, tunnel_name=None):
    """Serve an existing session database until the process stops."""
    app = app_module.create_app(db_path=db_path)
    _print_urls(f"http://{host}:{port}", app.state.session_id, app.state.admin_token, db_path)
    if tunnel:
        _start_cloudflared(port, app, domain=domain, tunnel_name=tunnel_name)

    # cloudflared connects from a Cloudflare IP that is in no default trusted
    # list, so Uvicorn needs telling to honour the X-Forwarded-* headers it
    # sends. Without this the app cannot work out its own public URL.
    uvicorn.run(app, host=host, port=port, proxy_headers=True,
                forwarded_allow_ips="*", log_level="info")


def _start_cloudflared(port: int, app, domain=None, tunnel_name=None):
    """Spawn cloudflared and set app.state.tunnel_url.

    Quick tunnel (default): watch stderr for the random *.trycloudflare.com
    address. Named tunnel (--domain): the hostname is fixed by your DNS
    route, so there is nothing to parse.

    The URL lives on app.state, not in the database, so it dies with the
    process and a later --db reopen never picks up a stale one.
    """
    cmd = ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"]
    if domain:
        cmd += ["run", tunnel_name or domain.split(".")[0]]

    try:
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                text=True, bufsize=1)
    except FileNotFoundError:
        typer.echo("cloudflared not found — install it or pass --no-tunnel")
        return None

    atexit.register(_terminate_quietly, proc)
    token = app.state.admin_token

    if domain:
        app.state.tunnel_url = f"https://{domain}"
        _print_tunnel(app.state.tunnel_url, token)
        return proc

    def watch():
        tail: list[str] = []
        deadline = time.time() + 30
        for line in proc.stderr or ():
            tail = (tail + [line])[-20:]
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                app.state.tunnel_url = m.group(0)
                _print_tunnel(m.group(0), token)
                return
            if time.time() > deadline:
                break
        typer.echo("Tunnel URL not detected. cloudflared said:")
        for raw in tail:
            typer.echo(f"  {raw.rstrip()}")
        typer.echo("Set the URL by hand in /admin, or restart with --no-tunnel.")

    threading.Thread(target=watch, daemon=True).start()
    return proc


def _terminate_quietly(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except Exception:
        pass


def _print_tunnel(url: str, token: str) -> None:
    typer.echo(f"\nTunnel:           {url}")
    typer.echo(f"Tunnel present:   {url}/present?token={token}\n")


def _print_urls(base: str, session_id: str, token: str, db_path: Path) -> None:
    # typer.echo flushes; plain print would not, and uvicorn.run then blocks
    # forever so a buffered line would never reach a piped log.
    for line in (
        "",
        f"Session:          {session_id}",
        f"Local join:       {base}/join",
        f"Local admin:      {base}/admin?token={token}",
        f"Present:          {base}/present",
        f"Present (drive):  {base}/present?token={token}",
        f"Database:         {db_path}",
        "",
    ):
        typer.echo(line)
