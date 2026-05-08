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
from . import questions as questions_mod

cli = typer.Typer(no_args_is_help=True, help="Lykkepoller: minimal live polling for presentations.")

DATA_DIR = Path("data")


@cli.command()
def run(
    yaml_path: Path = typer.Argument(
        None, exists=True, dir_okay=False, readable=True, help="Question YAML file."
    ),
    db: Path = typer.Option(
        None,
        "--db",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Reopen an existing session database.",
    ),
    migrate_questions: Path = typer.Option(
        None,
        "--migrate-questions",
        exists=True,
        dir_okay=False,
        readable=True,
        help="When reopening with --db, replace the question snapshot with this YAML.",
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    no_tunnel: bool = typer.Option(False, "--no-tunnel", help="Skip cloudflared tunnel (offline/manual)."),
    domain: str | None = typer.Option(
        None,
        "--domain",
        help="Use a named cloudflared tunnel and advertise https://DOMAIN (e.g. lykkepoller.com).",
    ),
    tunnel_name: str | None = typer.Option(
        None,
        "--tunnel-name",
        help="Named-tunnel name. Defaults to the first label of --domain.",
    ),
):
    """Start the polling app for a YAML question file or a saved session DB."""
    if db is None and yaml_path is None:
        raise typer.BadParameter("provide YAML_PATH (new session) or --db (reopen).")
    if db is None and migrate_questions is not None:
        raise typer.BadParameter("--migrate-questions requires --db.")

    if db is not None:
        db_path = db
        if yaml_path is not None:
            raise typer.BadParameter(
                "provide either YAML_PATH (new session) or --db (reopen), not both."
            )
        if migrate_questions is not None:
            _do_migration(db_path, migrate_questions, assume_yes=yes)
        session = _peek_session(db_path)
        session_id = session["id"]
        admin_token = session["admin_token"]
    else:
        data = questions_mod.load(yaml_path)
        session_id = _friendly_id()
        admin_token = _friendly_id()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Filename embeds creation date and the YAML basename so `ls data/`
        # is self-explaining ("oh, that was the SAR-presentation"). The random
        # session id is still in there to disambiguate re-runs of the same YAML.
        date = time.strftime("%Y-%m-%d")
        slug = _yaml_slug(yaml_path)
        db_path = DATA_DIR / f"{date}-{slug}-{session_id}.sqlite"
        if db_path.exists():
            raise typer.BadParameter(f"{db_path} already exists. Use `--db {db_path}` to reopen.")
        conn = db_module.connect(db_path)
        db_module.init_schema(conn)
        db_module.create_session(
            conn,
            session_id,
            data["title"],
            data["questions"],
            admin_token,
            source_yaml_filename=yaml_path.name,
        )
        conn.close()

    fastapi_app = app_module.create_app(db_path=db_path)

    _print_urls(host, port, session_id, admin_token, db_path)

    if tunnel_name and not domain:
        raise typer.BadParameter("--tunnel-name requires --domain.")
    if not no_tunnel:
        _start_cloudflared(port, fastapi_app, domain=domain, tunnel_name=tunnel_name)

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


@cli.command()
def inspect(
    db_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
):
    """Print a quick summary of a session database."""
    conn = db_module.connect(db_path)
    session = db_module.get_session(conn)
    if session is None:
        typer.echo(f"No session found in {db_path}")
        raise typer.Exit(code=1)
    state = db_module.get_state(conn, session["id"])
    n_questions = len(session["questions"])
    n_responses = sum(
        db_module.count_responses(conn, session["id"], q["id"]) for q in session["questions"]
    )
    connected = db_module.count_connected(conn, session["id"])
    typer.echo(f"Session: {session['id']}")
    typer.echo(f"Title: {session['title']}")
    typer.echo(f"Source: {session.get('source_yaml_filename') or '(unknown)'}")
    typer.echo(f"Created: {session['created_at']}")
    typer.echo(f"Questions: {n_questions}")
    typer.echo(f"Responses: {n_responses}")
    typer.echo(f"Connected (last 30s): {connected}")
    typer.echo(f"Active question: {state['active_question_id'] or '-'}")
    typer.echo(f"Session ended: {'yes' if state['ended'] else 'no'}")
    typer.echo(f"Database: {db_path}")
    conn.close()


@cli.command()
def export(
    db_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    output: Path = typer.Option(..., "--output", "-o", help="CSV output path."),
):
    """Export all responses for a session to CSV."""
    from . import exports

    conn = db_module.connect(db_path)
    session = db_module.get_session(conn)
    if session is None:
        typer.echo(f"No session found in {db_path}")
        raise typer.Exit(code=1)
    csv_text = exports.csv_for_session(conn, session)
    output.write_text(csv_text)
    typer.echo(f"Wrote {output}")
    conn.close()


def _peek_session(db_path: Path) -> dict:
    conn = db_module.connect(db_path)
    db_module.init_schema(conn)
    session = db_module.get_session(conn)
    conn.close()
    if session is None:
        raise typer.BadParameter(f"{db_path} has no session row.")
    return session


def _do_migration(db_path: Path, yaml_path: Path, *, assume_yes: bool) -> None:
    """Apply --migrate-questions: load YAML, diff against snapshot, replace if confirmed.

    The merge / diff logic is in questions.diff_for_migration; this command
    just owns the user interaction (printing the summary, surfacing risks,
    asking for confirmation, writing the result).
    """
    new_qs = questions_mod.load(yaml_path)["questions"]

    conn = db_module.connect(db_path)
    db_module.init_schema(conn)
    session = db_module.get_session(conn)
    if session is None:
        conn.close()
        raise typer.BadParameter(f"{db_path} has no session row.")

    diff = questions_mod.diff_for_migration(session["questions"], new_qs)

    if diff["type_changes"]:
        conn.close()
        raise typer.BadParameter(
            "Cannot change question type for existing ids: "
            f"{diff['type_changes']}. Use new ids instead."
        )

    typer.echo("Migration summary:")
    typer.echo(f"  added:    {diff['added'] or '-'}")
    typer.echo(f"  updated:  {diff['updated'] or '-'}")
    typer.echo(f"  kept (only in DB): {diff['kept'] or '-'}")
    if diff["option_id_changes"]:
        typer.echo("")
        typer.echo("WARNING: the following multiple-choice questions changed option ids.")
        typer.echo("Existing responses for those options will appear in CSV but will not")
        typer.echo("aggregate cleanly because they reference ids that no longer exist.")
        for qid in diff["option_id_changes"]:
            typer.echo(f"  - {qid}")

    if not assume_yes:
        if not typer.confirm("Apply migration?", default=False):
            typer.echo("Aborted; snapshot unchanged.")
            conn.close()
            raise typer.Exit(code=1)

    db_module.replace_questions(conn, session["id"], diff["merged"])
    typer.echo("Migration applied.")
    conn.close()


def _yaml_slug(yaml_path: Path) -> str:
    """Sanitize a YAML stem for use in a filename.

    Keeps [A-Za-z0-9._-]; collapses other runs to a single dash. Case is
    preserved so "SAR-presentation.yaml" stays "SAR-presentation".
    """
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", yaml_path.stem)
    s = re.sub(r"-+", "-", s).strip("-_.")
    return s or "session"


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


def _start_cloudflared(
    port: int,
    fastapi_app,
    domain: str | None = None,
    tunnel_name: str | None = None,
):
    """Spawn cloudflared and set `fastapi_app.state.tunnel_url`.

    Two modes:
      - Quick tunnel (default): `cloudflared tunnel --url http://localhost:PORT`,
        watch stderr for the random `*.trycloudflare.com` URL.
      - Named tunnel (--domain): `cloudflared tunnel --url http://localhost:PORT
        run NAME`, advertise `https://DOMAIN` immediately. The hostname is fixed
        by your DNS route, so there's nothing to parse.

    Storing on app.state (not the DB) keeps the URL ephemeral: it dies with the
    process, so a later `--db` reopen never picks up a stale tunnel URL.
    """
    cmd = ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"]
    if domain:
        name = tunnel_name or domain.split(".")[0]
        cmd += ["run", name]

    try:
        proc = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        typer.echo("cloudflared not found — install it or use --no-tunnel")
        return None

    atexit.register(_terminate_quietly, proc)

    token = fastapi_app.state.admin_token
    if domain:
        url = f"https://{domain}"
        fastapi_app.state.tunnel_url = url
        typer.echo(f"\nTunnel:                  {url}")
        typer.echo(f"Tunnel present:          {url}/present")
        typer.echo(f"Tunnel present (drive):  {url}/present?token={token}\n")
        return proc

    def _watch():
        captured: list[str] = []
        found = False
        deadline = time.time() + 30
        for line in proc.stderr:
            captured.append(line)
            if len(captured) > 200:
                del captured[:100]
            if not found:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m:
                    url = m.group(0)
                    fastapi_app.state.tunnel_url = url
                    typer.echo(f"\nTunnel:                  {url}")
                    typer.echo(f"Tunnel present:          {url}/present")
                    typer.echo(f"Tunnel present (drive):  {url}/present?token={token}\n")
                    found = True
                elif time.time() > deadline:
                    break
        if not found:
            typer.echo("Tunnel URL not detected. cloudflared output:")
            for raw in captured[-20:]:
                typer.echo(f"  {raw.rstrip()}")
            typer.echo("Set URL manually in /admin or restart with --no-tunnel.")

    threading.Thread(target=_watch, daemon=True).start()
    return proc


def _terminate_quietly(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except Exception:
        pass


def _print_urls(host: str, port: int, session_id: str, admin_token: str, db_path: Path) -> None:
    base = f"http://{host}:{port}"
    print()
    print(f"Session:       {session_id}")
    print(f"Local join:    {base}/join")
    print(f"Local admin:   {base}/admin?token={admin_token}")
    print(f"Present:       {base}/present")
    print(f"QR:            {base}/qr.png")
    print(f"Database:      {db_path}")
    print()


def main() -> None:
    cli()
