# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Live polling for talks, run from the presenter's laptop. A quiz is a Python
file; a session is one SQLite file. Three pages: `/admin` (presenter),
`/present` (projector), `/join` (phones). No accounts, no build step, no
database server.

`README.md` is the user-facing reference. Update it when you change behaviour
it describes.

## Commands

```bash
uv sync
uv run quizzes/example_quiz.py --no-tunnel   # start a session (no phones)
uv run lykkepoller run --db data/<f>.sqlite  # reopen a saved session
uv run lykkepoller inspect data/<f>.sqlite
uv run lykkepoller export data/<f>.sqlite -o out.csv

uv run pytest
uv run pytest tests/test_quiz.py::test_name  # one test
uv run pytest -k reveal
uv run ruff check .                          # line length 100
uv run python -m lykkepoller.quiz            # quiz.py's demo block
```

## Architecture

**One process = one session = one SQLite file.** `create_app(db_path=...)` opens
one shared connection and reads the single session row. There is no
multi-session state anywhere.

**Two entry points, one job each.** A quiz script starts a *new* session
(`quiz.run()` → `serve.new_session_db()` → `serve.serve()`). The `lykkepoller`
CLI only *reopens* (`run --db`), inspects and exports. `serve.py` exists so the
library never has to import the CLI.

**The database is the only live state.** Routes read SQLite on every request and
render server-side. `static/app.js` polls a JSON endpoint every 1.5s and reloads
the page when the active question changes — there is no client-side templating.
New behaviour belongs in a route plus a template, not in JS.

**All SQL lives in `db.py`.** Never write SQL inline elsewhere. Two response
tables by design: `unique_responses` (UNIQUE constraint = answer once, for
multiple choice and rating) and `append_responses` (append-only, for free text).
The table a row lands in is what makes an answer one-shot or repeatable — not a
column that could disagree with the question definition.

**Snapshot at session start.** `Quiz.to_questions()` produces plain dicts that
go into `sessions.questions_json`. The quiz file is never imported again, so
editing it mid-session changes nothing. Answers are tied to what was actually
asked. Don't break this while refactoring.

**The authoring layer and the runtime speak different types.** `quiz.py`'s
dataclasses exist only until `to_questions()` runs. Everything downstream —
`app.py`, `db.py`, `exports.py`, the templates — reads the stored dicts.
`find_question` and `option_label` at the bottom of `quiz.py` operate on those
dicts, not the dataclasses.

**Free text and the vote types reveal in opposite directions.** `activate()` in
`app.py` opens free text with `reveal_free_text` on (it starts empty, so there is
nothing to leak) and multiple choice off (bars would let the room see the leader
and follow it). Rejection (`rejected_free_text`) exists so "approve all" can skip
what the presenter crossed out — "not yet approved" alone could not express that.

**Validation happens in `__post_init__`,** so errors name the field and point at
the line in the user's quiz file. Hand-written `if` statements, no framework.
This is the project's main guard rail — when adding a field, add its check.

**Deck position is derived, not stored.** It is the index of
`state.active_question_id` in the questions list (`question_index` in `app.py`).
An earlier version stored a parallel slide list; that was the same information
twice.

**Schema changes need an `ALTER TABLE`.** `CREATE TABLE IF NOT EXISTS` will not
add a column to an older database, and old sessions are meant to reopen. Add the
column to `SCHEMA` *and* an `_ensure_column(...)` line in `init_schema`.

**The public URL is worked out per request** by `compute_base_url`: manual
override on the session row → tunnel URL from the cloudflared watcher →
`X-Forwarded-*` headers → the request itself. It returns a `source` label shown
on `/admin`, so a wrong join URL can be diagnosed instead of guessed at.

**The admin token** arrives in the URL, moves to a cookie on first visit, then
redirects to a clean `/admin`. It keeps the audience from clicking the
presenter's buttons; it is not real security, and the code says so.

**Keyboard shortcuts submit forms** rather than calling an API, so the
server-side handler stays the single path through which state changes.

**Themes** are CSS variable files in `static/themes/`. They set `--theme-*`,
which `body.present` maps onto `--fg/--bg/--muted/--accent`. A theme therefore
restyles the whole projector page without any component knowing themes exist.
`/admin` and `/join` are deliberately left alone.

## Before you start

```bash
git config core.hooksPath tools/githooks   # once per clone: enables the pre-push check
```

`tools/smoke.sh` drives a real session end to end — uvicorn, static assets and
their cache headers, cookies across redirects, the QR redraw, the CSV. Run it
after anything touching a route, a template or `app.js`; `pytest` uses
TestClient and never starts a server, so it cannot see those. Every bug that
reached a browser this far has been invisible to pytest and visible here.

```bash
tools/smoke.sh          # 22 checks, ~6s, exits 1 on failure, cleans up after itself
tools/smoke.sh 9001 quizzes/my_quiz.py
```

## The knowledge graph

`tools/kg.py` scans the repo into a SQLite graph — modules, functions, routes,
tables, templates, CSS classes, JS functions, and the edges between them.
Use it before reading files: it answers cross-language questions in one query
that would otherwise take several greps and a lot of context.

```bash
uv run tools/kg.py build                  # force a rebuild (derived; gitignored)
uv run tools/kg.py map                    # compact overview of the whole repo
uv run tools/kg.py trace /admin/reject    # a route: handler, tables, callers
uv run tools/kg.py node compute_results   # one thing, both directions
uv run tools/kg.py impact db.end_session  # what breaks if I change it
uv run tools/kg.py check                  # seams that have come apart
uv run tools/kg.py sql "SELECT ..."       # the graph is just two tables
```

It rebuilds itself when any scanned file is newer than the graph (~0.2s), so
you never have to remember `build` — a stale graph answers confidently and
wrongly, which is worse than no graph.

**Read `check` output with judgement.** Python extraction is exact (`ast`);
templates, JS and CSS are regex, so those findings point at a file rather than
proving anything. The two questions it answers with certainty — does every form
action hit a real route, does every JS selector match markup something renders —
are pinned in `tests/test_graph.py` instead. Both failure modes are silent in
the browser, which is how they survived a whole refactor unnoticed.

## Where things are

```text
src/lykkepoller/
  quiz.py      Quiz, MultipleChoice, Rating, FreeText + validation
  serve.py     session file creation, cloudflared, uvicorn
  app.py       every route, state helpers, result aggregation
  db.py        all the SQL, schema, migration hooks
  cli.py       reopen / inspect / export
  exports.py   CSV
  templates/   base, participant, admin, present
  static/      style.css, app.js, themes/
```

## Tests

`tests/test_db.py` is deliberately heavy — the database is the state machine.
`tests/test_app.py` drives the app over HTTP with `TestClient` and is where
behaviour changes get pinned down. `tests/test_quiz.py` covers every guard rail
in the authoring layer.

## House style here

- Short docstrings. Comments explain *why*, not *what*. This codebase was
  compressed on purpose — don't re-inflate it.
- Flat and explicit over clever. No new abstractions on speculation.
- The library files end with an `if __name__ == "__main__":` demo block where
  one helps (`quiz.py` has one).
