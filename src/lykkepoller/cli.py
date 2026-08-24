"""The `lykkepoller` command: run a saved quiz, reopen, inspect, export.

A Python quiz script starts its own session (`uv run my_quiz.py`). This
command covers the rest: starting a session from a `.lykkepoll` file, and
everything that happens to a session file afterwards.

`run` takes one of two things, and they are different jobs:
  --file  a saved quiz  -> a new session, no answers in it yet
  --db    a saved session -> the same session back, answers and all
"""

import json
import sqlite3
from pathlib import Path

import typer

from . import db as db_module
from . import exports as exports_mod
from . import serve as serve_mod
from . import spec as spec_mod

cli = typer.Typer(
    no_args_is_help=True,
    help="Run a saved quiz; reopen, inspect and export saved sessions.",
)


def _open(db_path: Path) -> tuple[sqlite3.Connection, dict]:
    conn = db_module.connect(db_path)
    db_module.init_schema(conn)
    try:
        session = db_module.get_session(conn)
    except sqlite3.OperationalError as e:
        # The presenter's page was renamed /admin -> /drive, and the column
        # holding its token with it. Sessions written before that cannot be
        # read, and saying so beats a traceback about a missing column.
        typer.secho(f"{db_path}: {e}", fg="red", err=True)
        typer.secho("This session predates the /admin -> /drive rename and "
                    "cannot be reopened.", fg="red", err=True)
        raise typer.Exit(code=1) from None
    if session is None:
        typer.secho(f"{db_path} has no session in it.", fg="red", err=True)
        raise typer.Exit(code=1)
    return conn, session


@cli.command()
def run(
    db: Path = typer.Option(
        None, "--db", exists=True, dir_okay=False, help="Saved session to reopen."
    ),
    file: Path = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False,
        help="Saved quiz (.lykkepoll) to start a new session from.",
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    no_tunnel: bool = typer.Option(False, "--no-tunnel", help="Local only; skip cloudflared."),
    domain: str | None = typer.Option(None, "--domain", help="Advertise https://DOMAIN."),
    tunnel_name: str | None = typer.Option(None, "--tunnel-name", help="Named tunnel to run."),
):
    """Start a session from a saved quiz (--file), or reopen a saved one (--db)."""
    if tunnel_name and not domain:
        raise typer.BadParameter("--tunnel-name needs --domain.")
    if bool(db) == bool(file):
        raise typer.BadParameter("Pass one of --db (reopen a session) or --file (run a quiz).")

    if file:
        db = serve_mod.new_session_db(_read_quiz(file), source_name=file.name)
        typer.echo(f"New session from {file}")
    else:
        conn, _ = _open(db)
        conn.close()
    serve_mod.serve(db_path=db, host=host, port=port, tunnel=not no_tunnel,
                    domain=domain, tunnel_name=tunnel_name)


@cli.command()
def validate(quiz_file: Path = typer.Argument(..., exists=True, dir_okay=False)):
    """Check a .lykkepoll file and say what is in it."""
    quiz = _read_quiz(quiz_file)
    counts: dict[str, int] = {}
    for q in quiz.questions:
        counts[q.type] = counts.get(q.type, 0) + 1
    typer.secho(f"{quiz_file} is a valid quiz file.", fg="green")
    for line in (
        f"Title:     {quiz.title}",
        f"Theme:     {quiz.theme}",
        f"Version:   {quiz.schema_version}",
        f"Questions: {len(quiz.questions)} "
        f"({', '.join(f'{n} {t}' for t, n in sorted(counts.items()))})",
    ):
        typer.echo(line)


@cli.command()
def schema():
    """Print the .lykkepoll format as JSON Schema, for tools in other languages."""
    typer.echo(json.dumps(spec_mod.json_schema(), indent=2))


def _read_quiz(path: Path) -> spec_mod.QuizSpec:
    """Load a .lykkepoll file, or stop with the reasons it is not one."""
    try:
        return spec_mod.load(path)
    except spec_mod.QuizFileError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(code=1) from None


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
