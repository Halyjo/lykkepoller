# Lykkepoller

Minimal local-first live polling app for presentations and teaching.

You run a local app, share a QR code, activate questions, collect answers,
see results. The presenter operates everything from `/admin`; the audience
sees results on `/present`; participants answer on `/join`.

## What this is

- A small FastAPI app driven by a YAML question file -- or by a YAML deck
  of HTML slides with questions interleaved (see "Talks with slides").
- One SQLite database file per session (in `data/`).
- A `/present` page for the projector, a `/join` page for participants,
  an `/admin` control page.
- Two short polling loops in plain JS (no framework, no build step).
- ~700 lines of Python and ~250 lines of JS/CSS.

## What this is not

- Not a Kahoot/Mentimeter/Slido clone.
- No accounts, no scoring, no leaderboard, no realtime websockets.
- No frontend build system. No ORM.

## Install

Requires Python 3.11+, [`uv`](https://docs.astral.sh/uv/) and cloudflared. 
```bash
uv sync
```

## Run a local demo

```bash
uv run lykkepoller run questions/demo_questions.yaml
```

The terminal prints something like:

```
Session:          blue-otter-4281
Local join:       http://127.0.0.1:8000/join
Local admin:      http://127.0.0.1:8000/admin?token=winter-fox-1934
Present:          http://127.0.0.1:8000/present
Present (drive):  http://127.0.0.1:8000/present?token=winter-fox-1934
QR:               http://127.0.0.1:8000/qr.png
Database:         data/2026-05-02-demo_questions-blue-otter-4281.sqlite
```

Open the admin URL on your laptop. Show `/present` on the projector. Open
`/join` on a phone (use a tunnel URL, see below).

`/join` redirects to `/join/<session-id>`; the session id is in the URL so
a phone that reconnects lands in the right session. Opening the "Present
(drive)" URL once sets the admin cookie on that browser, after which the
keyboard shortcuts work directly from the projector page.

The QR is also written to a file next to the session database
(`data/<same-name>.qr.png`) so you can paste it into a slide.

## Question file format

A session is driven by a single YAML file. The whole shape:

- **Root** — a mapping with two keys.
  - `title` *(string, required, non-empty)* — shown on every page.
  - `questions` *(list, required, non-empty)* — the ordered question set.
    A deck uses `slides:` here instead; the two are mutually exclusive
    (see "Talks with slides").
- **Each question** — a mapping with these keys:
  - `id` *(string, required, unique within the file)* — stable identifier;
    do not change it after a session has been created (existing responses
    are stored against this id).
  - `type` *(string, required)* — one of `multiple_choice`, `free_text`,
    `rating`.
  - `prompt` *(string, required)* — what the audience sees.
- **`multiple_choice` only:**
  - `options` *(list, required, non-empty)* — each option is a mapping with
    `id` *(string, unique within the question)* and `label` *(string)*. An
    optional `is_correct: true` flags one or more options as the correct
    answer (revealed by the `C` shortcut on `/admin`).
- **`rating` only:**
  - `steps` *(integer, required, 2..11)* — number of buttons on the scale.
  - `low_label` and `high_label` *(strings, required, non-empty)* — anchor
    text shown at the ends of the scale; intermediate steps are unlabeled.
- **`free_text`** has no extra fields.

Validation is light by design: a typo causing a clear runtime error is
fine. The full validator lives in `src/lykkepoller/questions.py` if you
want to read the rules verbatim.

## Making the questions

Write a yaml-file on the following format and link to it in the run command as shown above.
Three question types are supported: `multiple_choice`, `free_text`, and
`rating`.

```yaml
title: Demo
questions:
  - id: q1
    type: multiple_choice
    prompt: "Which color is better"
    options:
      - id: A
        label: "red"
        is_correct: true
      - id: B
        label: "blue"
        is_correct: true
      - id: C
        label: "yellow"
  - id: q2
    type: free_text
    prompt: "Suggest cool activities!"
  - id: q3
    type: rating
    prompt: "How well did you follow this section?"
    steps: 5            # number of buttons (2..11)
    low_label: "Lost"   # anchor on step 1
    high_label: "Got it" # anchor on step `steps`
```

Rating questions are one-shot (same lock as multiple-choice — no changing
your answer). The histogram on `/admin` and `/present` is gated on the
same `R` toggle as free-text answers, and the average is shown alongside
the response count when revealed.

## Talks with slides

A session can also drive a whole deck: HTML slides and questions in one
ordered list. Point `run` at the talk's YAML instead of a questions file:

```bash
uv run lykkepoller run talks/demo-presentation/talk.yaml
```

The layout of a talk directory:

```text
talks/demo-presentation/
  talk.yaml          # the deck: content slides and questions, in order
  slides/            # one HTML file per content slide
  theme.css          # optional; overrides the CSS variables for this talk
  themes/            # the bundled themes theme.css can @import
  images/            # anything slides reference
```

`talk.yaml` uses `slides:` instead of `questions:`. Each entry is either a
path to a slide file or an inline question -- the same question shape as
a questions-only YAML:

```yaml
title: "Feedback signals matter"
slides:
  - content: slides/01-title.html
  - content: slides/02-bullets.html
  - question:
      id: metric_goal
      type: multiple_choice
      prompt: "What happens if the metric does not reflect the real goal?"
      options:
        - {id: A, label: "The model still works"}
        - {id: B, label: "We do not know if the model works", is_correct: true}
  - content: slides/03-discussion.html
```

A slide file is plain HTML, rendered through Jinja with a set of macros
for the common shapes (`title_slide`, `bullets`, `image`, `math`, `table`,
`two_col`, `code_block`, `quote`, `callout`, `fragment`) -- see
`src/lykkepoller/slide_macros.py`. Plain HTML works too; the macros are a
convenience, not a wall.

`→` walks the deck one slide at a time. A question slide opens its
question; a content slide leaves the previous question open, so people
keep answering while you talk through the discussion slide that follows
it. `Esc` closes the question explicitly.

Slides are rendered once, when the session is created, and the resulting
HTML is stored in the session database -- same rule as questions. Editing
a slide file mid-talk changes nothing until you start a new session.

### Slide assets

Slides reference files relative to the talk directory, served under
`/talk/`: `image("images/plot.png")` becomes `/talk/images/plot.png`, and
`theme.css` is linked automatically when the talk has one.

`/talk/` is public -- anyone with the join link can request it -- so it
only serves asset file types: `.css`, images, fonts, video, and `.pdf`.
In particular it will not serve `talk.yaml`, which names the correct
multiple-choice options and every question you have not asked yet.

## Run with a public tunnel

By default, `lykkepoller run` spawns a Cloudflare Quick Tunnel for you and
picks up its public URL automatically. The terminal prints a `Tunnel:` line
once cloudflared is up:

```
Tunnel:        https://something.trycloudflare.com
```

Open that URL on your laptop and append the admin path printed earlier,
e.g. `https://something.trycloudflare.com/admin?token=winter-fox-1934`.

You need [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
on your `PATH`. To skip tunnelling entirely, pass `--no-tunnel`.

### Use your own domain

If you have a domain on Cloudflare with a [named tunnel][named] routed to
it, point `--domain` at the hostname:

```bash
uv run lykkepoller run questions/demo_questions.yaml --domain lykkepoller.com
```

This spawns `cloudflared tunnel --url http://localhost:8000 run <name>`,
where `<name>` defaults to the first DNS label of `--domain` (override with
`--tunnel-name`). The advertised URL is `https://lykkepoller.com`
immediately — no random subdomain to copy each time.

[named]: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/

### How the app discovers its public URL

The app *infers* the public base URL from the `X-Forwarded-Host` /
`X-Forwarded-Proto` headers cloudflared sends, so the QR code on
`/present` and the join URL shown on `/admin` automatically point at the
tunnel URL — no `--public-url` flag, no restart.

### The Uvicorn proxy-headers footgun

By default, Uvicorn ignores `X-Forwarded-*` headers unless explicitly told
to trust them. The `lykkepoller run` command starts Uvicorn with:

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
- **CONTENT SLIDE** (talks only, see "Talks with slides"): the rendered
  slide, with a small QR in the corner and the slide counter.
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

The participant page (`/join/<session-id>`) polls
`GET /api/participant/state/<session-id>` every ~1.5 seconds. That single endpoint:

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
| `C`            | toggle "show correct answer" for the current MC   |
| `A`            | approve all free-text answers submitted so far    |
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

## Showing the correct answer

Multiple-choice options accept an optional `is_correct: true` flag in the
YAML:

```yaml
- id: q1
  type: multiple_choice
  prompt: "Which metric best captures missed oil spills?"
  options:
    - id: A
      label: "Pixel accuracy"
    - id: B
      label: "Instance-wise recall"
      is_correct: true
    - id: C
      label: "Mean brightness"
```

By default the correct option is just stored, not shown -- you move on
with arrow keys without revealing anything. Press `C` (or click "Show
correct" on `/admin`) to color the correct option(s) green and gently
dim the others on both `/admin` and `/present`. Multiple options can be
flagged correct.

The reveal resets to off whenever a new question is activated, so
pressing `→` does not leak the next question's answer to the audience.

If a question has no `is_correct` on any option, the toggle is a no-op
for that question.

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

For bulk acceptance, press `A` (or click "Approve all so far") --
this approves every free-text answer submitted up to that moment for
the active question. It is idempotent: when more answers come in, you
can press `A` again to accept the new batch without affecting earlier
choices. If you want to exclude a specific answer, just unapprove it
individually after the bulk-approve.

## Export CSV

From the admin page, click "Download CSV". From the command line:

```bash
uv run lykkepoller export data/<session-id>.sqlite --output responses.csv
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
uv run lykkepoller inspect data/<session-id>.sqlite
```

Prints session id, title, number of questions, number of responses,
connected count, active question, ended flag, and database path. Useful
when something looks off and you want a quick look without writing SQL.

## Reopen an existing session

A presentation tool must survive restart. Each session lives in its own
SQLite file. The filename embeds the creation date and the YAML basename
so `ls data/` is self-explaining:

```text
data/2026-05-02-SAR-presentation-blue-otter-4281.sqlite
data/2026-05-02-questions-amber-owl-7104.sqlite
data/2026-05-09-week2-quiz-quiet-raven-3320.sqlite
```

Reopen with:

```bash
uv run lykkepoller run --db data/2026-05-02-SAR-presentation-blue-otter-4281.sqlite
```

(`lykkepoller inspect <db>` also prints the source YAML filename it was
created from, in case the file was renamed.)

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
uv run lykkepoller run --db data/blue-otter-4281.sqlite \
  --migrate-questions questions/demo_questions.yaml
```

The migration prints a summary (added / updated / kept) and asks for
confirmation. Pass `--yes` to skip the prompt.

Migration rules:

- Questions are matched by `id`.
- An id present in both: prompt and options are taken from the new YAML.
- An id only in the DB: kept; existing responses still appear in CSV
  export and in the admin/present results.
- An id only in the new YAML: appended, and given a question slide at the
  end of the deck so it shows up on `/admin` and is reachable with `→`.
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
src/lykkepoller/
  __init__.py
  cli.py          # typer commands: run / inspect / export
  app.py          # FastAPI routes, session/state helpers
  questions.py    # YAML load + validate + migration diff
  db.py           # all SQL lives here, with comments
  qr.py           # qr.png helper
  exports.py      # CSV export
  slide_macros.py # bullets(), image(), math(), ... for slide files
  slide_render.py # runs slide files through Jinja at session-creation time
  templates/      # base.html, participant.html, admin.html, present.html
  static/         # style.css, app.js
questions/
  demo_questions.yaml
talks/
  demo-presentation/    # talk.yaml + slides/ + theme.css + images/
data/
  <date>-<yaml>-<session-id>.sqlite      (one per session)
  <date>-<yaml>-<session-id>.qr.png      (join QR, for pasting into slides)
tests/
  test_questions.py / test_db.py / test_app.py / test_exports.py / test_migration.py
```

## License

See `LICENSE`.
