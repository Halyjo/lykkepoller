# poller

Minimal local-first live polling app for presentations and teaching.

You run a local app, share a QR code, activate questions, collect answers, see results.
This is not a Kahoot/Mentimeter/Slido clone — it is intentionally small and easy to read.

## What this is

- A FastAPI app driven by a YAML question file.
- One SQLite database file per session (in `data/`).
- A `/present` page for the projector, a `/join` page for participants, an `/admin` control page.
- Two short polling loops in JS (no framework).

## What this is not

- No accounts, no scoring, no leaderboard, no realtime websockets.
- No frontend build system. No ORM.

## Install

```bash
uv sync
```

## Run

```bash
uv run poller run examples/questions.yaml
```

The terminal prints a session id, local join URL, local admin URL (with token),
present URL, and QR URL. Open the admin URL in your browser.

## Run with a public tunnel

```bash
uv run poller run examples/questions.yaml
# in another terminal:
cloudflared tunnel --url http://localhost:8000
```

Cloudflare prints a temporary public URL. Open the tunnel URL in the browser
and append the admin path printed in the local terminal.

The Uvicorn process is started with `proxy_headers=True, forwarded_allow_ips="*"`.
That is required because Cloudflare connects from an IP not in any default
trusted list — without it, the app would silently fall back to `localhost` when
inferring the public URL behind cloudflared.

## Tests / lint

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```
