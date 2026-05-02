import secrets
from pathlib import Path

import typer
import uvicorn

from . import app as app_module
from . import db as db_module
from . import questions as questions_mod

cli = typer.Typer(no_args_is_help=True, help="Poller: minimal live polling for presentations.")

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
        db_path = DATA_DIR / f"{session_id}.sqlite"
        if db_path.exists():
            raise typer.BadParameter(f"{db_path} already exists. Use `--db {db_path}` to reopen.")
        conn = db_module.connect(db_path)
        db_module.init_schema(conn)
        db_module.create_session(conn, session_id, data["title"], data["questions"], admin_token)
        conn.close()

    fastapi_app = app_module.create_app(db_path=db_path)

    _print_urls(host, port, session_id, admin_token, db_path)

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

    Migration rules (kept in sync with the README):
      - questions matched by id; existing responses stay tied to id.
      - id only in DB: kept (responses still appear in CSV / UI).
      - id only in YAML: added.
      - id in both: prompt + options replaced from YAML.

    Risks printed before confirmation:
      - changing option ids orphans previous responses for that question.
      - changing a question's `type` for an existing id is not supported.
    """
    new_data = questions_mod.load(yaml_path)
    new_qs = new_data["questions"]

    conn = db_module.connect(db_path)
    db_module.init_schema(conn)
    session = db_module.get_session(conn)
    if session is None:
        conn.close()
        raise typer.BadParameter(f"{db_path} has no session row.")

    old_qs = session["questions"]
    old_by_id = {q["id"]: q for q in old_qs}
    new_by_id = {q["id"]: q for q in new_qs}

    added = [qid for qid in new_by_id if qid not in old_by_id]
    removed_kept = [qid for qid in old_by_id if qid not in new_by_id]
    updated = [qid for qid in new_by_id if qid in old_by_id]

    type_changes = [qid for qid in updated if old_by_id[qid]["type"] != new_by_id[qid]["type"]]
    if type_changes:
        conn.close()
        raise typer.BadParameter(
            f"Cannot change question type for existing ids: {type_changes}. Use new ids instead."
        )

    option_id_changes = []
    for qid in updated:
        if old_by_id[qid]["type"] == "multiple_choice":
            old_opts = {o["id"] for o in old_by_id[qid].get("options", [])}
            new_opts = {o["id"] for o in new_by_id[qid].get("options", [])}
            if old_opts != new_opts:
                option_id_changes.append(qid)

    typer.echo("Migration summary:")
    typer.echo(f"  added:    {added or '-'}")
    typer.echo(f"  updated:  {updated or '-'}")
    typer.echo(f"  kept (only in DB): {removed_kept or '-'}")
    if option_id_changes:
        typer.echo("")
        typer.echo("WARNING: the following questions changed option ids.")
        typer.echo("Existing responses for those options will appear in CSV but will not")
        typer.echo("aggregate cleanly because they reference ids that no longer exist.")
        for qid in option_id_changes:
            typer.echo(f"  - {qid}")

    # Merge: yaml wins for matched ids; db-only ids are preserved at the end.
    merged = []
    seen = set()
    for q in new_qs:
        merged.append(q)
        seen.add(q["id"])
    for q in old_qs:
        if q["id"] not in seen:
            merged.append(q)

    if not assume_yes:
        if not typer.confirm("Apply migration?", default=False):
            typer.echo("Aborted; snapshot unchanged.")
            conn.close()
            raise typer.Exit(code=1)

    db_module.replace_questions(conn, session["id"], merged)
    typer.echo("Migration applied.")
    conn.close()


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
