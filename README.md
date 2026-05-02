# poller

Minimal local-first live polling app for presentations and teaching.

You run a local app, share a QR code, activate questions, collect answers,
see results. The presenter operates everything from `/admin`; the audience
sees results on `/present`; participants answer on `/join`.

## What this is

- A small FastAPI app driven by a YAML question file.
- One SQLite database file per session (in `data/`).
- A `/present` page for the projector, a `/join` page for participants,
  an `/admin` control page.
- Two short polling loops in plain JS (no framework, no build step).
- ~700 lines of Python and ~250 lines of JS/CSS.

## What this is not

- Not a Kahoot/Mentimeter/Slido clone.
- No accounts, no scoring, no leaderboard, no realtime websockets.
- No frontend build system. No ORM.
- No automated tunnel management -- you run cloudflared yourself.

## Install

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Run a local demo

```bash
uv run poller run examples/questions.yaml
```

The terminal prints something like:

```
Session:       blue-otter-4281
Local join:    http://127.0.0.1:8000/join
Local admin:   http://127.0.0.1:8000/admin?token=winter-fox-1934
Present:       http://127.0.0.1:8000/present
QR:            http://127.0.0.1:8000/qr.png
Database:      data/blue-otter-4281.sqlite
```

Open the admin URL on your laptop. Show `/present` on the projector. Open
`/join` on a phone (use a tunnel URL, see below).

## Run with Cloudflare Quick Tunnel

Local-only is rarely useful for an audience. Pair this with a Cloudflare
Quick Tunnel:

```bash
# terminal 1
uv run poller run examples/questions.yaml

# terminal 2
cloudflared tunnel --url http://localhost:8000
```

Cloudflare prints a temporary public URL, e.g.
`https://something.trycloudflare.com`. Open the tunnel URL on your laptop
and append the admin path printed in terminal 1, e.g.
`https://something.trycloudflare.com/admin?token=winter-fox-1934`.

The app *infers* the public base URL from the `X-Forwarded-Host` /
`X-Forwarded-Proto` headers cloudflared sends, so the QR code on
`/present` and the join URL shown on `/admin` automatically point at the
tunnel URL -- no `--public-url` flag, no restart.

### The Uvicorn proxy-headers footgun

By default, Uvicorn ignores `X-Forwarded-*` headers unless explicitly told
to trust them. The `poller run` command starts Uvicorn with:

```python
uvicorn.run(app, host=host, port=port,
            proxy_headers=True, forwarded_allow_ips="*")
```

`forwarded_allow_ips="*"` is necessary because cloudflared connects from a
Cloudflare IP that is not in any default trusted list. Without it the app
silently falls back to `localhost` when reading headers behind cloudflared.

The admin page shows the inferred join URL with a small "source" label
(`override` / `headers` / `localhost` / `request`) so you can verify what
the app is using without guessing.

If automatic detection ever fails or you want to pin a specific URL, use
the **Public URL override** field on the admin page. It is stored on the
session row and survives restart.

## `/present`

Show this on the projector when the audience needs to scan the QR or see
live results.

- **IDLE** state: large QR code + join URL + connected count.
- **QUESTION_ACTIVE**: the active question, response count, connected
  count, and live result bars (multiple choice). For free text, only the
  response count is shown unless you flip the global "Reveal free-text on
  /present" toggle on `/admin` *and* the answer has been individually
  approved (see Free-text moderation, below).
- **ENDED**: large "Thanks!" message.

## Admin token + cookie

The admin URL printed at startup looks like
`/admin?token=winter-fox-1934`. The first request:

1. validates the token,
2. sets an `httponly` cookie containing it, and
3. **redirects to plain `/admin`** so the token disappears from the
   address bar (avoids leaking on screen share).

Subsequent requests authenticate via the cookie. POST endpoints
(`/admin/activate`, `/admin/end`, etc.) are cookie-only. If the cookie is
missing or wrong, the page returns 401.

The token is stored in the session DB and reused across restarts -- a
bookmarked admin URL keeps working after `--db` reopen.

This is *not* serious security. It is only to prevent casual audience
members from changing questions if someone glimpses the address bar.

## Participant polling and heartbeat

The participant page (`/join`) polls `GET /api/participant/state` every
~1.5 seconds. That single endpoint:

- updates `participants.last_seen_at` for the participant's anonymous
  cookie (this is the heartbeat),
- returns the current state (idle / active / ended) and active question.

When the active question changes, or the session ends, the page reloads
itself so the server template renders the new state. Connected count on
`/admin` and `/present` is computed as
`COUNT(DISTINCT participant_id) WHERE last_seen_at > now - 30s`.

Admin and `/present` poll their own state endpoints similarly. Admin
re-renders the result blocks in place; `/present` updates counts and
re-renders the active result block.

## Keyboard shortcuts (admin page)

When focus is *not* in an input or textarea:

| key            | action                                            |
|----------------|---------------------------------------------------|
| `→` or `Space` | activate next question (end session if on last)  |
| `←`            | activate previous question                        |
| `Esc`          | clear active question (back to IDLE)              |
| `R`            | toggle "reveal free-text answers on /present"     |
| `E`            | end session (with confirmation)                   |

The shortcuts simply submit the matching `<form>` so the server-side
handler is the single source of truth. A quick manual test:

1. Open `/admin?token=...`, then plain `/admin`.
2. Press `→` -- first question activates.
3. Press `→` repeatedly -- moves through; on the last question, prompts to
   end.
4. Press `Esc` -- back to IDLE.
5. Click the override URL field, type a character, press `→` -- nothing
   happens (focus check works).

## Free-text moderation

Free-text answers are protected by **two** gates, so an off-color answer
cannot accidentally hit the projector.

1. **Global reveal toggle** on `/admin` (or press `R`): controls whether
   `/present` shows any free-text answers at all. Default off.
2. **Per-answer approve toggle**: each free-text answer on `/admin` has
   an "Approve for /present" button. Only approved answers are shown,
   and only when the global toggle is on.

By default `/present` shows the response count only ("17 responses
received"). The presenter reads the answers on `/admin`, approves the
ones they want to show, then flips the global toggle.

## Export CSV

From the admin page, click "Download CSV". From the command line:

```bash
uv run poller export data/<session-id>.sqlite --output responses.csv
```

Columns:

```
session_id,question_id,question_type,prompt,participant_id,answer,answer_label,created_at
```

`answer_label` is the human-readable label for multiple-choice answers
(looked up from the question snapshot stored on the session row), and is
empty for free-text rows or for orphaned multiple-choice rows whose
option id is no longer present.

## Inspect a database

```bash
uv run poller inspect data/<session-id>.sqlite
```

Prints session id, title, number of questions, number of responses,
connected count, active question, ended flag, and database path. Useful
when something looks off and you want a quick look without writing SQL.

## Reopen an existing session

A presentation tool must survive restart. Each session lives in its own
SQLite file:

```bash
uv run poller run --db data/blue-otter-4281.sqlite
```

On reopen:

- the session id, title, admin token, and question snapshot are
  preserved,
- the previously active question stays active (so a mid-lecture restart
  comes back to where you were),
- if the session was ended, it stays ended -- you must explicitly
  reactivate a question,
- a bookmarked admin URL keeps working (the token is reused).

The local admin/join/present URLs are reprinted with the new port.

## Migrate to an updated YAML

By default, reopening with `--db` ignores the YAML file: the snapshot
stored in SQLite is what was asked, and the snapshot wins. To opt in to
changes:

```bash
uv run poller run --db data/blue-otter-4281.sqlite \
  --migrate-questions examples/questions.yaml
```

The migration prints a summary (added / updated / kept) and asks for
confirmation. Pass `--yes` to skip the prompt.

Migration rules:

- Questions are matched by `id`.
- An id present in both: prompt and options are taken from the new YAML.
- An id only in the DB: kept; existing responses still appear in CSV
  export and in the admin/present results.
- An id only in the new YAML: appended.
- Existing responses stay tied to their `question_id`.

### Risks (printed at migration time)

- **Changing option `id`s on a multiple-choice question orphans existing
  responses for that question.** They will appear in CSV export but will
  not aggregate cleanly because they reference option ids that no longer
  exist. To fix labels, change the `label` field, not the `id`.
- **Changing a question's `type` for an existing `id` is rejected.**
  Use a new `id` instead.
- This feature is intended for small fixes (typos, clarifications), not
  for redesigning a session mid-run.

## Tests / lint

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

The database tests in `tests/test_db.py` are deliberately exhaustive --
the DB is the single source of truth for session state, and writing this
in plain SQL is worth a thick test belt. The HTTP integration tests in
`tests/test_app.py` exercise the full lifecycle (admin auth, state
machine, answer submission, results, CSV export, public URL inference).

## File layout

```text
src/poller/
  __init__.py
  cli.py          # typer commands: run / inspect / export
  app.py          # FastAPI routes, session/state helpers
  questions.py    # YAML load + validate + migration diff
  db.py           # all SQL lives here, with comments
  qr.py           # qr.png helper
  exports.py      # CSV export
  templates/      # base.html, participant.html, admin.html, present.html
  static/         # style.css, app.js
examples/
  questions.yaml
data/
  <session-id>.sqlite  (one per session)
tests/
  test_questions.py / test_db.py / test_app.py / test_exports.py / test_migration.py
```

## License

See `LICENSE`.
