# Project spec: Minimal live polling app for presentations

## Goal

Build a small local-first live polling web app for presentations and teaching.

The presenter runs a local app, shares a QR code / URL, activates questions, collects answers, and sees simple results.

This is not a Kahoot, Mentimeter, or Slido clone. The product should be tiny, readable, and easy to modify later.

Main priorities:

1. Useful while standing in front of an audience.
2. Small codebase.
3. Minimal setup.
4. Simple local storage.
5. Easy Marp/Typst integration later, but not at the cost of MVP complexity.

---

## Core presentation workflow

Expected MVP workflow:

```bash
uv run poller run examples/questions.yaml
```

Then in another terminal:

```bash
cloudflared tunnel --url http://localhost:8000
```

Cloudflare prints a temporary public URL.

The app should not require `--public-url` for normal use.

Instead:

- admin/presenter pages should infer the public base URL from the current browser request when accessed through the tunnel
- QR code and join links should update based on that detected public URL
- if auto-detection fails, the admin page should have a simple manual public URL override field

This avoids restarting the app after the tunnel URL is created.

Practical use:

1. Start app locally.
2. Start Cloudflare Quick Tunnel.
3. Open the tunnel URL on the presenter laptop.
4. Go to `/admin?token=...` (token visible only on first open; the app then sets a cookie and redirects to a clean `/admin`).
5. Show `/present` on projector when audience needs to scan QR code.
6. Activate questions with big buttons or keyboard shortcuts.
7. Participants answer on phones.
8. Presenter sees results and connected-participant count.
9. Export CSV.

---

## Strong design constraints

Use simple Python, simple HTML templates, simple CSS, and tiny JavaScript.

Do not over-engineer.

Avoid:

- React/Vue/Svelte
- frontend build systems
- SQLAlchemy or other ORM
- WebSockets in MVP
- automated tunnel management in MVP
- account system
- leaderboard/scoring/gamification
- strict enum-heavy domain model
- deep abstraction layers
- unnecessary wrapper classes
- complex plugin architecture

Plain strings are fine for question types. A typo causing a clear test/runtime error is acceptable if it keeps the code much smaller.

A few longer straightforward functions are acceptable if they make the flow easier to understand. Do not split code into many tiny helper functions unless it genuinely improves clarity.

---

## Tooling

Use:

- `uv` for environment/dependency management
- `ruff` for formatting/linting
- `pytest` for tests

Required commands:

```bash
uv sync
uv run poller run examples/questions.yaml
uv run pytest
uv run ruff check .
uv run ruff format .
```

Configure these in `pyproject.toml`.

---

## Suggested stack

Use:

- FastAPI
- Uvicorn
- Jinja2
- Typer
- PyYAML
- qrcode[pil]
- standard-library `sqlite3`

Keep dependencies minimal.

---

## Minimal repository structure

```text
poller/
  pyproject.toml
  README.md

  src/
    poller/
      __init__.py
      cli.py
      app.py
      questions.py
      db.py
      qr.py
      exports.py

      templates/
        base.html
        admin.html
        participant.html
        present.html

      static/
        style.css
        app.js

  examples/
    questions.yaml

  data/
    .gitkeep

  tests/
    test_questions.py
    test_db.py
    test_exports.py
    test_migration.py
```

Do not add more structure unless clearly needed.

---

## Question format

Use YAML.

Example:

```yaml
title: "Demo polling session"

questions:
  - id: q1
    type: multiple_choice
    prompt: "Which metric best captures missed oil spills?"
    options:
      - id: A
        label: "Pixel accuracy"
      - id: B
        label: "Instance-wise recall"
      - id: C
        label: "Mean brightness"

  - id: q2
    type: free_text
    prompt: "What can go wrong if the metric does not match the real goal?"
```

MVP question types:

- `multiple_choice`
- `free_text`

Numeric questions are deliberately deferred. Add them only after MVP is proven useful in a real lecture.

Use light validation:

- `title` must exist
- `questions` must exist and be non-empty
- each question needs `id`, `type`, and `prompt`
- multiple-choice questions need non-empty `options`
- option entries need `id` and `label`
- question `id`s must be unique within a session
- option `id`s must be unique within a question

Do not build a complicated schema system.

---

## Question state machine

The session has three states. Implement them explicitly. The participant page, admin page, and `/present` page are all driven by this state.

States:

- **IDLE**: no question is active.
  - Participant page: "Waiting for presenter to start a question."
  - `/present`: large QR code + join URL.
- **QUESTION_ACTIVE**: a question is active.
  - Participant page: shows the question, accepts answers.
  - `/present`: shows the question, response count, and live result bars (for multiple choice).
- **ENDED**: session is over.
  - Participant page: "Thanks, that's the last one."
  - `/present`: shows a final summary screen (or falls back to QR).

Transitions:

- IDLE → QUESTION_ACTIVE: presenter activates any question.
- QUESTION_ACTIVE → QUESTION_ACTIVE: presenter activates a different question (Next/Previous, or click in the question list).
- QUESTION_ACTIVE → IDLE: presenter explicitly clears the active question.
- QUESTION_ACTIVE → ENDED: presenter clicks "End session", or presses Next while on the last question.
- ENDED → QUESTION_ACTIVE: presenter activates any question (reopens session).

Notes:

- For MVP, results are always visible to both presenter and audience whenever a question is active. A "blind answer / reveal results" toggle is a deliberate post-MVP feature; do not implement it now.
- The post-session leaderboard / personal-score view is also deferred. The ENDED state is just a static "thanks" message in MVP, but the state is named so the future hook is obvious.

---

## Storage

Use SQLite internally.

Use CSV for export.

Important: the SQL/database layer must be especially simple, explicit, commented, and well-tested.

Rules:

- keep all SQL in `db.py`
- do not spread SQL across the app
- do not use an ORM
- add plain-English comments above each table/query
- write tests that make the behavior obvious

Use one SQLite database per session:

```text
data/<session-id>.sqlite
```

Use friendly generated session IDs, for example:

```text
blue-otter-4281
```

The friendly phrase is only for local session naming. It is not a public URL.

Suggested minimal schema:

```sql
-- One row per session. The questions snapshot is stored as JSON so that
-- changes to the YAML file after the session starts do not silently
-- alter what was asked. Use --migrate-questions to opt in to changes.
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    public_url_override TEXT,
    admin_token TEXT NOT NULL
);

-- Tracks which question (if any) is currently active, and whether the
-- session has been ended. Drives the state machine.
CREATE TABLE IF NOT EXISTS state (
    session_id TEXT PRIMARY KEY,
    active_question_id TEXT,
    ended INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- One row per (session, question, participant). UNIQUE constraint
-- enforces "one answer per participant per question" — duplicate submits
-- replace the previous answer with INSERT ... ON CONFLICT DO UPDATE.
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    UNIQUE(session_id, question_id, participant_id)
);

-- Heartbeat table: every participant poll updates last_seen_at.
-- Used to compute the live "connected" count shown on /admin and /present.
CREATE TABLE IF NOT EXISTS participants (
    session_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (session_id, participant_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Stores free-text answers that the presenter has approved for display
-- on /present. Admin sees all answers; /present only sees approved ones.
CREATE TABLE IF NOT EXISTS approved_free_text (
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    response_id INTEGER NOT NULL,
    PRIMARY KEY (session_id, question_id, response_id),
    FOREIGN KEY (response_id) REFERENCES responses(id)
);
```

Duplicate answer behavior:

- one answer per participant per question
- participant identity is an anonymous browser cookie
- if the same participant answers the same question again, replace their previous answer

Connected participant count:

- compute as `COUNT(DISTINCT participant_id) WHERE last_seen_at > now - 30 seconds`
- update `participants.last_seen_at` on every poll from the participant page

---

## Database tests

Database tests are high priority because I do not know SQL well.

Add tests for:

- schema creation
- creating a session
- saving question snapshot
- reading question snapshot
- setting active question
- reading active question
- transitioning to ENDED state and back
- inserting a response
- replacing duplicate response from same participant/question
- allowing different participants to answer same question
- allowing same participant to answer different questions
- aggregating multiple-choice results
- recording participant heartbeats
- counting connected participants within a recent time window
- approving and unapproving a free-text response
- exporting responses to CSV
- reopening an existing database file after restart
- migrating a session with new question YAML (see Migration section)

The database layer should be small enough that these tests cover nearly all important behavior.

---

## JavaScript requirements

Use as little JavaScript as possible.

Two small polling loops are important for usability:

1. Participant page polls every 1-2 seconds for active-question changes (and the poll doubles as a heartbeat).
2. Admin/presenter results poll every 1-2 seconds for response/participant counts.

Keep JS in:

```text
static/app.js
```

Add educational comments explaining:

- what endpoint is polled
- how often it polls
- what happens when the active question changes
- what happens when results update
- how the heartbeat updates the connected count

Do not introduce a JS framework.

Do not write clever frontend state management.

Use simple `fetch()` calls.

### Keyboard shortcuts (admin and present pages)

When focus is on the admin page, the following shortcuts must work:

- `→` / `Space`: activate next question (or end session if on last)
- `←`: activate previous question
- `Esc`: clear active question (return to IDLE)
- `R`: toggle whether free-text responses are revealed on `/present`
- `E`: end session (with confirmation)

Shortcuts should be ignored when focus is in an input/textarea (so typing in the override URL field does not trigger them).

`/present` does not need shortcuts — it has no controls.

Add a small amount of testing or manual validation notes for this behavior in the README, since I do not know much JavaScript.

---

## Main pages

Exact route names can be adjusted if it simplifies the code, but keep the product behavior.

### `/join`

Participant page.

Behavior:

- if state is IDLE, show "Waiting for presenter to start a question."
- if state is QUESTION_ACTIVE, show the active question
- participant can submit an answer
- after submission, show submitted answer clearly
- participant can change/resubmit answer for the current question
- page polls every 1-2 seconds and updates without manual refresh when:
  - a new question becomes active
  - the session ends
- if state is ENDED, show "Thanks, that's the last one." A future version may show the participant's score and a leaderboard; for MVP, a static thank-you is enough.

### `/admin?token=<token>`

Presenter control page.

Must be fast and uncluttered.

Show:

- session title
- join URL (the inferred public URL, with a small note showing where the value came from)
- QR code
- **connected count**: e.g. "12 connected · 8 answered" (refreshed by polling)
- active question (visually obvious — different background or border)
- large Previous Question button
- large Next Question button
- large End Session button
- list of questions with activate buttons
- result summary (live counts and bars for MC; list of answers for free text, each with an "Approve for /present" toggle)
- export CSV link
- public URL override field
- toggle: "Reveal free-text answers on /present" (also bound to keyboard shortcut `R`)

Important UI requirements:

- The presenter should be able to operate this quickly during a lecture. Big buttons. Minimal clutter. Current active question must be visually obvious.
- All major actions must be reachable by keyboard (see shortcuts above).
- Do not include the admin token in any visible URL on the page once the cookie is set.

### `/present`

Projector-friendly page.

This is for the audience/projector, not for control.

It shows one of the following depending on state:

- **IDLE**: large QR code + join URL + connected count
- **QUESTION_ACTIVE**: the active question, response count, connected count, and live result bars (for MC). For free text: by default show only the response count ("17 responses received"); only show the actual answers if the presenter has flipped the "reveal free-text answers" toggle on `/admin`. Even then, only show answers that have been individually approved by the presenter.
- **ENDED**: large "Thanks!" message; optionally fall back to QR for late stragglers

No admin controls.

No token.

No clutter.

Large typography.

This page is product-critical because it avoids relying on QR codes embedded in slides when tunnel URLs are temporary.

### `/qr.png`

Serve a QR code image.

The QR should point to the participant join URL.

Base URL priority:

1. manual public URL override from admin, if set
2. detected base URL from current request
3. localhost fallback

This route reduces friction with file paths and slide-relative image locations.

---

## Public URL handling

Do not require `--public-url` as the default workflow.

Instead, implement helper logic:

```text
get_base_url(request):
  if public_url_override exists:
      use it
  else if request has forwarded proto/host headers:
      use those
  else:
      use request base URL
```

Headers to consider:

- `Host`
- `X-Forwarded-Proto`
- `X-Forwarded-Host`

### Important Uvicorn footgun

By default, Uvicorn **ignores** `X-Forwarded-Proto` and `X-Forwarded-Host` headers unless explicitly told to trust them. If you do not set this, header-reading code will look correct but silently fall back to `localhost` behind cloudflared.

The fix: when starting Uvicorn programmatically (which the `poller run` command does), pass:

```python
uvicorn.run(
    app,
    host=host,
    port=port,
    proxy_headers=True,
    forwarded_allow_ips="*",
)
```

Cloudflared connects from a Cloudflare IP that is not in any default trusted list, so `forwarded_allow_ips="*"` is necessary in this local-tunnel use case. Document this clearly in the README and in a comment above the uvicorn call.

The admin page should always display the currently inferred join URL with a small note showing whether it came from the override, the request headers, or localhost — so I can verify it without guessing.

---

## Admin token

Generate a random admin token at session creation and store it in the `sessions` table. Print the admin URL in the terminal:

```text
Session:       blue-otter-4281
Local join:    http://localhost:8000/join
Local admin:   http://localhost:8000/admin?token=winter-fox-1934
Present:       http://localhost:8000/present
QR:            http://localhost:8000/qr.png
Database:      data/blue-otter-4281.sqlite
```

### Cookie-based session

To avoid leaking the admin token during screen sharing:

1. When a request to `/admin` arrives with a valid `?token=...` query parameter, the server sets an `httponly` cookie containing the token and immediately responds with a redirect to plain `/admin` (no query string).
2. Subsequent requests to `/admin` (and any admin-only POST endpoints) authenticate using the cookie.
3. If the cookie is missing or invalid and no token is in the URL, return 401 with a short message ("Open the admin URL printed in your terminal").
4. If the token is wrong, return 401 — do not redirect.

Notes:

- The cookie should be `httponly` (JavaScript cannot read it).
- `secure` flag should be set when the request came in over HTTPS (which it will, behind cloudflared).
- The token is reused across restarts (it is stored in the DB), so a bookmarked admin URL keeps working after reopening with `--db`.
- Participant URLs never include the token.
- This is not serious security. It is only to prevent casual audience members from changing questions if someone glimpses the address bar.

---

## Results

Keep result display simple.

For multiple choice:

- count per option
- percentage per option
- simple HTML/CSS bars

For free text:

- admin sees all submitted answers as a list, each with an "Approve for /present" toggle
- `/present` shows only the response count by default
- if the presenter flips the "Reveal free-text answers on /present" toggle (`R` shortcut), `/present` shows the answers that have been individually approved
- this two-step gating (global toggle + per-answer approval) protects against an off-color answer appearing on the projector before the presenter has read it

No Chart.js unless it is genuinely simpler than plain HTML/CSS.

---

## CSV export

Support:

```bash
uv run poller export data/<session-id>.sqlite --output responses.csv
```

Also provide an admin export link.

Suggested columns:

```csv
session_id,question_id,question_type,prompt,participant_id,answer,answer_label,created_at
```

`answer_label` is filled in for multiple-choice rows (looked up from the question snapshot) and is empty for free-text rows.

Keep export logic in `exports.py`.

---

## Inspect/debug command

Add a simple command so I can inspect session state without knowing SQL:

```bash
uv run poller inspect data/<session-id>.sqlite
```

Example output:

```text
Session: blue-otter-4281
Title: Demo polling session
Questions: 2
Responses: 42
Connected (last 30s): 8
Active question: q2
Session ended: no
Database: data/blue-otter-4281.sqlite
```

This is important for debugging.

---

## Restart and session recovery

This is not optional. A presentation tool must survive restart without losing everything.

Support reopening an existing database:

```bash
uv run poller run --db data/<session-id>.sqlite
```

Behavior on reopen:

- load existing session from SQLite
- keep all previous responses
- **keep the previously active question** (if you restart mid-session, you come back to where you were — this is the right default for the "something went wrong" case)
- if the session was ENDED, it stays ENDED (presenter must explicitly reactivate a question)
- reuse the stored admin token, so a bookmarked admin URL keeps working
- print fresh local admin/join/present URLs

### Migrating to an updated YAML

If you reopen with `--db` and the YAML has changed since the session was created, the **default behavior is to ignore the YAML**. The DB question snapshot wins. A warning is printed showing that the YAML differs from the snapshot.

To explicitly migrate to the new YAML:

```bash
uv run poller run --db data/<session-id>.sqlite --migrate-questions examples/questions.yaml
```

Migration rules:

- Match questions by `id`.
- If a question `id` exists in both the DB snapshot and the new YAML: update prompt and options to the new YAML.
- If a question `id` exists only in the DB: keep its snapshot; existing responses are still tied to it and still appear in the UI and in CSV export.
- If a question `id` exists only in the new YAML: add it.
- Existing responses are kept and tied to `question_id`.

Documented risks (printed at migration time and in the README):

- **Changing option `id`s in a multiple-choice question will orphan existing responses for that question.** The old responses will still appear in CSV export, but they will not aggregate cleanly in the admin/present results because they reference option `id`s that no longer exist. If you need to fix option labels, change the `label` field, not the `id`.
- **Changing a question's `type` for an existing `id` is not supported.** Use a new `id` instead.
- This feature is intended for small fixes (typos, clarifications), not redesigning a session mid-run.

The migration command should print a summary of what changed and ask for confirmation before writing, unless `--yes` is passed.

---

## QR and slide integration

MVP should not depend on embedding static QR images into slides.

Default live workflow:

- show `/present` on projector when audience should scan QR
- `/present` displays current QR and join URL

Still useful:

- `/qr.png` serves the current QR code
- admin page shows simple copyable snippets

Marp snippet:

```markdown
![Poll QR](http://localhost:8000/qr.png)
```

Typst snippet:

```typst
#image("http://localhost:8000/qr.png", width: 35%)
```

These snippets are mostly for later stable URL workflows. With temporary tunnel URLs, `/present` is the main low-friction solution.

Do not modify slide files automatically in MVP.

Future ideas, not MVP:

- Typst or Marp-driven question activation from slides
- Per-participant score view and leaderboard at the ENDED state

---

## CLI

Required commands:

```bash
uv run poller run examples/questions.yaml
uv run poller run examples/questions.yaml --host 0.0.0.0 --port 8000
uv run poller run --db data/<session-id>.sqlite
uv run poller run --db data/<session-id>.sqlite --migrate-questions examples/questions.yaml [--yes]
uv run poller export data/<session-id>.sqlite --output responses.csv
uv run poller inspect data/<session-id>.sqlite
```

Do not overbuild CLI.

---

## README requirements

README should explain:

1. What this is.
2. What this is not.
3. Install with `uv`.
4. Run local demo.
5. Run with Cloudflare Quick Tunnel (mention `proxy_headers=True, forwarded_allow_ips="*"`).
6. How to use `/present`.
7. How the admin token + cookie redirect works.
8. How participant polling and the heartbeat work.
9. Keyboard shortcuts on the admin page.
10. Free-text moderation flow on `/present`.
11. How to export CSV.
12. How to inspect a database.
13. How to reopen an existing session.
14. How to migrate to an updated YAML, including the option-id risk.
15. How to run tests and ruff.

Include the real recommended MVP workflow:

```bash
uv run poller run examples/questions.yaml
```

In another terminal:

```bash
cloudflared tunnel --url http://localhost:8000
```

Then open the public tunnel URL in the browser and append the admin path with token (printed in the local terminal).

---

## Implementation milestones

Build in this order.

### Milestone 1: Local skeleton

Acceptance criteria:

- `uv run poller run examples/questions.yaml` starts app
- `/join`, `/admin?token=...`, `/present` all return pages
- admin lists questions
- generated admin token works (query param accepted; cookie redirect can come in M5)

### Milestone 2: SQLite and recovery

Acceptance criteria:

- app creates `data/<session-id>.sqlite`
- schema is created (sessions, state, responses, participants, approved_free_text)
- session, admin token, and question snapshot are stored
- `uv run poller inspect ...` works
- `uv run poller run --db ...` reopens existing session, keeps active question, reuses admin token
- database tests pass

### Milestone 3: State machine, activation, and answers

Acceptance criteria:

- IDLE / QUESTION_ACTIVE / ENDED states implemented in DB and UI
- admin can activate a question, end the session, and clear the active question
- Previous/Next buttons and keyboard shortcuts work
- participant page shows the right thing in each state
- participant can submit answers
- submitted answer is visible after submission
- resubmission replaces previous answer
- duplicate behavior is tested

### Milestone 4: Results, moderation, and CSV

Acceptance criteria:

- admin shows result summary (MC bars, free-text list with Approve toggles)
- `/present` shows results in QUESTION_ACTIVE
- multiple-choice counts/percentages work
- free-text moderation: count-only by default; per-answer approval; global reveal toggle (`R` shortcut)
- CSV export works
- export tests pass

### Milestone 5: Live polling, heartbeats, QR, and admin cookie

Acceptance criteria:

- participant page detects new active question and ENDED transition without manual refresh
- admin results update automatically
- participant heartbeats update the `participants` table
- "connected" count shown on `/admin` and `/present` and updates live
- JavaScript is small and commented
- `/qr.png` works
- `/present` shows large QR/join URL in IDLE
- inferred public URL is visible on admin page with provenance label
- manual public URL override works
- `proxy_headers=True, forwarded_allow_ips="*"` is set for Uvicorn
- admin token cookie + redirect implemented; token no longer visible in `/admin` URL after first hit

### Milestone 6: Migration, README, polish

Acceptance criteria:

- `--migrate-questions` works with the documented rules and risk warnings
- migration tests pass
- README contains exact working commands
- `uv run pytest` passes
- `uv run ruff check .` passes
- `uv run ruff format .` has been run
- code remains small and direct

Stop here for MVP.

---

## Definition of done

The MVP is done when:

1. I can define questions in YAML (multiple choice and free text).
2. I can run the app locally with `uv`.
3. I can expose it using Cloudflare Quick Tunnel, and the inferred public URL works without restart.
4. I can open admin through the tunnel; the token disappears from the URL after first load.
5. I can show `/present` on the projector.
6. Participants can scan QR code.
7. Participants do not need to manually refresh for new questions or for session end.
8. I can activate questions quickly with large buttons or keyboard shortcuts.
9. Participants can answer from phones.
10. Participants can see their submitted answer.
11. I can see live result summaries and a connected-participant count.
12. Free-text answers do not appear on `/present` until I have approved them.
13. I can export CSV.
14. I can inspect database state without SQL.
15. I can restart/reopen an existing session and come back to the question I was on.
16. I can migrate to an updated YAML when I want to.
17. Tests cover the database behavior, including migration.
18. Ruff and pytest pass.

The code should remain small enough that I can understand the project structure in less than 10 minutes.