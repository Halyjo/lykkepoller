# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Live polling for talks, run from the presenter's laptop. A quiz is a Python
file or a saved `.lykkepoll` file; a session is one SQLite file. Three pages:
`/admin` (presenter), `/present` (projector), `/join` (phones). No accounts,
no build step, no database server.

`README.md` is the user-facing reference. Update it when you change behaviour
it describes.

## Commands

```bash
uv sync
uv run quizzes/example_quiz.py --no-tunnel   # start a session (no phones)
uv run lykkepoller run --file q.lykkepoll    # start one from a saved quiz
uv run lykkepoller validate q.lykkepoll      # check a saved quiz
uv run lykkepoller schema                    # the format as JSON Schema
uv run lykkepoller run --db data/<f>.sqlite  # reopen a saved session
uv run lykkepoller inspect data/<f>.sqlite
uv run lykkepoller export data/<f>.sqlite -o out.csv

uv run pytest
uv run pytest tests/test_quiz.py::test_name  # one test
uv run pytest -k reveal
uv run ruff check .                          # line length 100
uv run python -m lykkepoller.quiz            # quiz.py's demo block
uv run python -m lykkepoller.spec            # spec.py's demo block
```

## Architecture

**One process = one session = one SQLite file.** `create_app(db_path=...)` opens
one shared connection and reads the single session row. There is no
multi-session state anywhere.

**One way into a new session: a QuizSpec.** `serve.new_session_db(spec, ...)`
takes nothing else. A Python quiz gets there via `quiz.run()` →
`Quiz.to_spec()`; a saved file via `lykkepoller run --file` → `spec.load()`.
So a session can never hold questions that would not survive being written to
a file. `serve.py` exists so the library never has to import the CLI.

**`run --db` and `run --file` are different jobs.** `--db` reopens a session,
answers and all; `--file` starts a fresh one from a saved quiz. An earlier
version of this file said the CLI only ever reopens — that changed when the
file format arrived, because a language-independent quiz file you cannot run
without Python is not language-independent.

**The database is the only live state.** Routes read SQLite on every request and
render server-side. `static/app.js` polls a JSON endpoint every 1.5s and reloads
the page when the active question changes — there is no client-side templating.
New behaviour belongs in a route plus a template, not in JS.

**All SQL lives in `db.py`.** Never write SQL inline elsewhere. Two response
tables by design: `unique_responses` (UNIQUE constraint = answer once, for
multiple choice and rating) and `append_responses` (append-only, for free text).
The table a row lands in is what makes an answer one-shot or repeatable — not a
column that could disagree with the question definition.

**Snapshot at session start.** `QuizSpec.to_questions()` produces plain dicts
that go into `sessions.questions_json`. The quiz file is never read again, so
editing it mid-session changes nothing. Answers are tied to what was actually
asked. Don't break this while refactoring.

**The stored snapshot and the `.lykkepoll` file are the same shape.** Both are
`QuizSpec` questions, so there is one shape to learn and no translation layer
to drift. Changing a field means bumping `SCHEMA_VERSION` in `spec.py` *and*
thinking about old session files, which are read with no validation because
the runtime wrote them itself.

**The authoring layer and the runtime speak different types.** `quiz.py`'s
dataclasses exist only until `to_spec()` runs. Everything downstream —
`app.py`, `db.py`, `exports.py`, the templates — reads the stored dicts.
`find_question` and `option_label` at the bottom of `quiz.py` operate on those
dicts, not the dataclasses. There is no loader from a file back into the
dataclasses: a file may carry option ids they would re-letter.

**Free text and the vote types reveal in opposite directions.** `activate()` in
`app.py` opens free text with `reveal_free_text` on (it starts empty, so there is
nothing to leak) and multiple choice off (bars would let the room see the leader
and follow it). Rejection (`rejected_free_text`) exists so "approve all" can skip
what the presenter crossed out — "not yet approved" alone could not express that.

**Two gates, two audiences.** `quiz.py` validates in `__post_init__` with
hand-written `if`s, so errors name the field, point at the line in the user's
Python, and can say "Did you mean 'Blue'?". `spec.py` validates `.lykkepoll`
files with pydantic, because those arrive from outside and the job is to
reject a bad one completely and list every problem at once. Don't merge them:
pydantic would make the authoring errors worse, and hand-written checks would
make the file errors patchier. When adding a field, add it to both.

`extra="forbid"` on the spec models is deliberate — a mistyped `"lowlabel"`
that was silently ignored would show up as a blank projector label mid-talk.

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

## Where this is going

One quiz format, several ways to fill it in. Python is the only comfortable
authoring interface today; the next one is a page in the app for making,
changing and deleting quizzes — working name `/quizzes`, a plain noun so it
covers all three, and not `/manage` or `/admin`, since `/admin` already means
the presenter's controls during a session.

What that page needs already holds, and should keep holding:

- `QuizSpec` is the only way into a new session, so a second author cannot
  invent a shape the runtime does not read.
- Files are checked on the way in, so a browser-made quiz is refused for the
  same reasons a hand-written one is.
- `run --file` does not care who wrote the file, and the CSV does not either.

So the page is a `QuizSpec` editor that writes `.lykkepoll` files. It is not a
reason to add a second storage path, a quizzes table, or a session that reads
its questions from anywhere but `sessions.questions_json`.

The Python API stays. It is the fastest way to write a quiz and the only one
with a real editor behind it — a browser form is an addition, not a
replacement.

## Before you start

```bash
git config core.hooksPath tools/githooks   # once per clone: enables the pre-push check
```

`tools/smoke.sh` finds a multiple-choice and a free-text question in whatever
quiz it is given, rather than assuming which id is which — hardcoding `q1` and
`q2` is what silently broke it when the example quiz gained a question at the
top. It drives a real session end to end — uvicorn, static assets and
their cache headers, cookies across redirects, the QR redraw, the CSV. Run it
after anything touching a route, a template or `app.js`; `pytest` uses
TestClient and never starts a server, so it cannot see those. Every bug that
reached a browser this far has been invisible to pytest and visible here.

```bash
tools/smoke.sh          # 23 checks, ~6s, exits 1 on failure, cleans up after itself
tools/smoke.sh 9001 quizzes/my_quiz.py
tools/smoke.sh 9001 quizzes/my_quiz.lykkepoll   # drives `run --file` instead
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
  spec.py      QuizSpec: the .lykkepoll format, pydantic-checked
  serve.py     session file creation, cloudflared, uvicorn
  app.py       every route, state helpers, result aggregation
  db.py        all the SQL, schema, migration hooks
  cli.py       run --file / reopen / inspect / export / validate / schema
  exports.py   CSV
  templates/   base, participant, admin, present
  static/      style.css, app.js, themes/
```

## Tests

`tests/test_db.py` is deliberately heavy — the database is the state machine.
`tests/test_app.py` drives the app over HTTP with `TestClient` and is where
behaviour changes get pinned down. `tests/test_quiz.py` covers every guard rail
in the authoring layer, and `tests/test_spec.py` every way a `.lykkepoll` file
can be wrong.

## House style here

- Short docstrings. Comments explain *why*, not *what*. This codebase was
  compressed on purpose — don't re-inflate it.
- Flat and explicit over clever. No new abstractions on speculation.
- The library files end with an `if __name__ == "__main__":` demo block where
  one helps (`quiz.py` and `spec.py` have one).
