"""The `lykkepoller` command: reopen, inspect and export saved sessions.

Starting a new session is the quiz script's job (`uv run my_quiz.py`).
This is for everything that happens to a session file afterwards.
"""

import sqlite3
from pathlib import Path

import typer

from . import db as db_module
from . import exports as exports_mod
from . import serve as serve_mod

cli = typer.Typer(no_args_is_help=True, help="Reopen, inspect and export saved sessions.")


def _open(db_path: Path) -> tuple[sqlite3.Connection, dict]:
    conn = db_module.connect(db_path)
    db_module.init_schema(conn)
    session = db_module.get_session(conn)
    if session is None:
        typer.secho(f"{db_path} has no session in it.", fg="red", err=True)
        raise typer.Exit(code=1)
    return conn, session


@cli.command()
def run(
    db: Path = typer.Option(..., "--db", exists=True, dir_okay=False, help="Session to reopen."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    no_tunnel: bool = typer.Option(False, "--no-tunnel", help="Local only; skip cloudflared."),
    domain: str | None = typer.Option(None, "--domain", help="Advertise https://DOMAIN."),
    tunnel_name: str | None = typer.Option(None, "--tunnel-name", help="Named tunnel to run."),
):
    """Reopen a saved session: same id, token, questions and answers."""
    if tunnel_name and not domain:
        raise typer.BadParameter("--tunnel-name needs --domain.")
    conn, _ = _open(db)
    conn.close()
    serve_mod.serve(db_path=db, host=host, port=port, tunnel=not no_tunnel,
                    domain=domain, tunnel_name=tunnel_name)


@cli.command()
def inspect(db_path: Path = typer.Argument(..., exists=True, dir_okay=False)):
    """Summarise a session file without opening it."""
    conn, s = _open(db_path)
    state = db_module.get_state(conn, s["id"])
    answers = sum(db_module.count_responses(conn, s["id"], q["id"]) for q in s["questions"])
    for line in (
        f"Session:   {s['id']}",
        f"Title:     {s['title']}",
        f"Source:    {s.get('source_filename') or '(unknown)'}",
        f"Created:   {s['created_at']}",
        f"Theme:     {s['theme']}",
        f"Questions: {len(s['questions'])}",
        f"Answers:   {answers}",
        f"Connected: {db_module.count_connected(conn, s['id'])} (last 30s)",
        f"Open now:  {state['active_question_id'] or '-'}",
        f"Ended:     {'yes' if state['ended'] else 'no'}",
    ):
        typer.echo(line)
    conn.close()


@cli.command()
def export(
    db_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    output: Path = typer.Option(..., "--output", "-o", help="CSV to write."),
):
    """Write every answer to a CSV, one row each."""
    conn, s = _open(db_path)
    output.write_text(exports_mod.csv_for_session(conn, s))
    typer.echo(f"Wrote {output}")
    conn.close()


def main() -> None:
    cli()
