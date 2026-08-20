# Lykkepoller

Live polling for talks and teaching, run from your own laptop.

You start the app, the audience scans a QR code, you open questions one at
a time and show the answers on the projector. A talk can also be a full
slide deck with the questions built into it.

- One YAML file describes the session.
- One SQLite file per session, in `data/`.
- Three pages: `/admin` for you, `/present` for the projector, `/join` for
  phones.
- No accounts, no scoring, no build step, no database server.

**New here?** [Getting started](GETTING_STARTED.md) walks through one talk,
start to finish. This page is the reference.

## Install

You need Python 3.11+, [`uv`](https://docs.astral.sh/uv/), and
[`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
if you want phones to reach your laptop.

```bash
uv sync
```

## Run

```bash
uv run lykkepoller run questions/demo_questions.yaml
```

The terminal prints:

```
Session:          blue-otter-4281
Local join:       http://127.0.0.1:8000/join
Local admin:      http://127.0.0.1:8000/admin?token=winter-fox-1934
Present:          http://127.0.0.1:8000/present
Present (drive):  http://127.0.0.1:8000/present?token=winter-fox-1934
QR:               http://127.0.0.1:8000/qr.png
Database:         data/2026-05-02-demo_questions-blue-otter-4281.sqlite
```

Open the admin URL on your laptop and put `/present` on the projector.
Phones need the tunnel URL, not `127.0.0.1` — see [Letting phones
in](#letting-phones-in).

Open **Present (drive)** once instead of plain `/present` and the keyboard
shortcuts work from the projector page too, so you never have to switch
windows. The token leaves the address bar right away.

The QR is also saved as a file next to the database
(`data/<same-name>.qr.png`) if you want it in a slide.

## The three pages

**`/admin`** — your control panel. The whole deck, live results, the
approve buttons, the join URL, and a CSV link.

**`/present`** — the projector. Big QR when nothing is running, then the
slide or the current question with its result bars. A small QR stays in
the corner so latecomers can still join.

**`/join`** — the phone page. Redirects to `/join/<session-id>`. It shows
a waiting screen until you open a question.

## Writing questions

Three types: `multiple_choice`, `free_text`, `rating`.

```yaml
title: Demo

questions:
  - id: q1
    type: multiple_choice
    prompt: "Which color is better?"
    options:
      - {id: A, label: "Red", is_correct: true}
      - {id: B, label: "Blue"}

  - id: q2
    type: free_text
    prompt: "Suggest a cool activity!"

  - id: q3
    type: rating
    prompt: "How well did you follow this section?"
    steps: 5
    low_label: "Lost"
    high_label: "Got it"
```

Every question needs `id`, `type` and `prompt`. The `id` is what answers
are stored against, so don't change it once a session has started.

- `multiple_choice` needs `options`, each with a unique `id` and a
  `label`. `is_correct: true` is optional and can be on more than one.
- `rating` needs `steps` (2 to 11) and the two end labels. The steps in
  between are unlabeled.
- `free_text` needs nothing else.

Multiple choice and rating are one-shot: once someone answers, their
answer is locked. Free text can be submitted as often as they like.

Validation is deliberately thin. The rules are in
`src/lykkepoller/questions.py` if you want to read them.

## Talks with slides

A session can be a whole deck: HTML slides and questions in one ordered
list. Point `run` at the talk instead of a questions file:

```bash
uv run lykkepoller run talks/demo-presentation/talk.yaml
```

A talk directory:

```text
talks/demo-presentation/
  talk.yaml     # the deck: slides and questions, in order
  slides/       # one HTML file per slide
  theme.css     # optional, sets the colors and fonts
  themes/       # the bundled themes theme.css can @import
  images/       # anything the slides show
```

`talk.yaml` uses `slides:` instead of `questions:`. Each entry is either a
slide file or a question, and questions look exactly as above:

```yaml
title: "Feedback signals matter"

slides:
  - content: slides/01-title.html
  - content: slides/02-bullets.html
  - question:
      id: metric_goal
      type: multiple_choice
      prompt: "What if the metric misses the real goal?"
      options:
        - {id: A, label: "The model still works"}
        - {id: B, label: "We can't tell if it works", is_correct: true}
  - content: slides/03-discussion.html
```

### Writing a slide

A slide is plain HTML. It also gets a few helpers, so the common shapes
are one line:

```html
{{ bullets(
     ["A model optimizes the signal you give it.",
      "Not the thing you care about.",
      "Those two come apart quietly."],
     title="Feedback signals matter",
) }}

{{ callout("Ask what the metric cannot see.", kind="note") }}
```

The helpers are `title_slide`, `bullets`, `image`, `math`, `table`,
`two_col`, `code_block`, `quote`, `callout` and `fragment`. They live in
`src/lykkepoller/slide_macros.py`, which is short and worth a look. Plain
HTML works anywhere — the helpers are a shortcut, not a fence.

`fragment(...)` hides a piece until you press `→`. Press again for the
next one; when they run out, `→` moves to the next slide.

`math("...")` is drawn by KaTeX, loaded from a CDN. Slides with math need
internet on the presenting laptop.

### Images and theme

Paths are relative to the talk directory and served under `/talk/`, so
`image("images/plot.png")` becomes `/talk/images/plot.png`. If the talk
has a `theme.css`, `/present` loads it by itself.

`/talk/` is public — anyone with the join link can read it — so it only
serves assets: CSS, images, fonts, video and PDF. It will **not** serve
`talk.yaml`, which holds the correct answers and every question you have
not asked yet.

A theme is a short file. Pick a bundled one, then override what you want:

```css
@import url("themes/notebook.css");

:root {
  --slide-accent: #b34700;
  --slide-font-headings: Georgia, serif;
}
```

Every name you can set is listed at the top of
`talks/demo-presentation/theme.css`.

### Running the deck

`→` moves one slide forward. A question slide opens its question; the
slides after it leave that question open, so people keep answering while
you talk through the discussion. `Esc` closes the question.

Slides are rendered once, when the session starts, and stored in the
database. Editing a slide file mid-talk changes nothing until you start a
new session.

## Keyboard shortcuts

They work on `/admin`, and on `/present` if you opened the drive URL. They
are ignored while you are typing in a text field.

| key            | what it does                                     |
|----------------|--------------------------------------------------|
| `→` or `Space` | next slide (ends the session after the last one) |
| `←`            | previous slide                                   |
| `Esc`          | close the current question                       |
| `R`            | show or hide the results on `/present`           |
| `C`            | show or hide the correct answer                  |
| `A`            | approve every free-text answer so far            |
| `E`            | end the session (asks first, `/admin` only)      |

Each key just submits the matching form, so the server stays in charge.

## Showing the correct answer

Mark options with `is_correct: true` and nothing happens until you press
`C`. Then the correct ones turn green and the rest dim, on both `/admin`
and `/present`.

The reveal turns itself off when the next question opens, so pressing `→`
never leaks the next answer.

## Free-text answers

Two gates, so nothing lands on the projector by accident:

1. **`R`** decides whether `/present` shows any answers at all. Off by
   default — the audience only sees a count.
2. **Approve** decides which ones. Each answer on `/admin` has a button.

Read them on `/admin`, approve the good ones, then press `R`.

`A` approves everything submitted so far. Press it again later to catch
the new ones; it won't undo your choices. To drop one, unapprove it
afterwards.

## Letting phones in

`lykkepoller run` starts a Cloudflare Quick Tunnel by itself and prints
the public URL when it comes up:

```
Tunnel:        https://something.trycloudflare.com
```

The QR code and the join URL on `/admin` follow that URL automatically.
Pass `--no-tunnel` to skip it.

With your own domain on Cloudflare and a [named tunnel][named] routed to
it, you get a stable address instead of a random subdomain:

```bash
uv run lykkepoller run talks/my-talk/talk.yaml --domain lykkepoller.com
```

The tunnel name defaults to the first part of the domain (`lykkepoller`);
use `--tunnel-name` to pick another.

[named]: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/

If the address ever looks wrong, `/admin` shows where it came from
(`tunnel`, `headers`, `override`, `localhost`) and has a field to type one
in by hand. What you type is saved and survives a restart.

## Reopening a session

Each session is its own SQLite file, named by date and source file:

```text
data/2026-05-02-SAR-presentation-blue-otter-4281.sqlite
```

Reopen it with:

```bash
uv run lykkepoller run --db data/2026-05-02-SAR-presentation-blue-otter-4281.sqlite
```

Everything comes back: id, title, admin token (so a bookmarked admin URL
still works), the deck, the answers, and the question you were on.

The YAML is not read again. What was asked is what was asked. To use an
edited file, start a new session.

## Getting the answers out

Click "Download CSV" on `/admin`, or:

```bash
uv run lykkepoller export data/<file>.sqlite --output responses.csv
```

One row per answer:

```
session_id,question_id,question_type,prompt,participant_id,answer,answer_label,created_at
```

`answer_label` is the readable option label for multiple choice, empty for
free text.

To see what is in a file without opening it:

```bash
uv run lykkepoller inspect data/<file>.sqlite
```

## The admin token

The admin URL carries a token. The first visit checks it, puts it in a
cookie, and redirects to plain `/admin` so it leaves the address bar
before you share your screen. After that the cookie does the work, and
`/present?token=...` sets the same cookie.

This keeps a curious audience member from clicking your buttons. It is not
real security.

## How it stays live

Phones poll `/api/participant/state/<session-id>` about once a second.
That same request is the heartbeat, so the connected count is just the
number of phones seen in the last 30 seconds. When the question changes,
the page reloads itself and the server renders the new state — there is no
client-side templating anywhere.

`/admin` and `/present` poll their own endpoints the same way.

## Tests

```bash
uv run pytest
uv run ruff check .
```

`tests/test_db.py` is thick on purpose: the database is the only source of
truth for session state, and it is written in plain SQL.
`tests/test_app.py` drives the whole thing over HTTP.

## Layout

```text
src/lykkepoller/
  cli.py            # run / inspect / export
  app.py            # routes and session state
  questions.py      # loading and validating YAML
  db.py             # all the SQL
  slide_macros.py   # bullets(), image(), math(), ...
  slide_render.py   # renders slide files when a session starts
  exports.py        # CSV
  qr.py             # QR codes
  templates/        # base, participant, admin, present
  static/           # style.css, app.js
questions/          # question-only YAML files
talks/              # talk.yaml + slides + theme, one per talk
data/               # one .sqlite and one .qr.png per session
tests/
```

## License

See `LICENSE`.
