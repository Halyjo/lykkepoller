# Lykkepoller

Live polling for talks and teaching, run from your own laptop.

You write a quiz in Python. The audience scans a QR code. You open one
question at a time and put the answers on the projector.

- One Python file per quiz. Your editor checks it as you type.
- Or one `.lykkepoll` file — plain JSON, no Python needed.
- One SQLite file per session, in `data/`.
- Three pages: `/admin` for you, `/present` for the projector, `/join` for phones.
- No accounts, no scoring, no build step, no database server.

## Install

You need Python 3.11+, [`uv`](https://docs.astral.sh/uv/), and
[`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
so phones can reach your laptop.

```bash
git clone https://github.com/Halyjo/lykkepoller && cd lykkepoller
brew install uv cloudflared
uv sync
```

## Write your quiz

Copy `quizzes/example_quiz.py` and edit it. Three question types:

```python
from lykkepoller import Quiz, MultipleChoice, Rating, FreeText

quiz = Quiz(
    title="My talk",
    theme="notebook",          # plain, teal, editorial, dark, notebook
    questions=[
        MultipleChoice(
            "Why do we need the loss function?",
            options=["To evaluate the model",
                     "To give feedback to the model",
                     "To initialize the model"],
            correct="To give feedback to the model",   # optional
        ),
        Rating("How well did you follow?", low="Lost", high="Got it", steps=5),
        FreeText("What would you check first?"),
    ],
)

if __name__ == "__main__":
    quiz.run()
```

- **MultipleChoice** — buttons. `correct=` is the text of the right answer, or
  a list of them. Leave it out for a poll with no right answer.
- **Rating** — a scale from 1 to `steps` (2–11). Only the ends are labelled.
- **FreeText** — they type. You choose what reaches the projector.

Multiple choice and rating lock once someone answers. Free text does not.

Mistakes are caught as the file loads, on the line that made them:

```
ValueError: correct='To initalize the model' is not an option for
'Why do we need the loss function?'. Did you mean 'To initialize the model'?
```

## Run it

```bash
uv run quizzes/my_quiz.py
```

It prints some URLs, then a `Tunnel:` line a moment later. Open two windows:

- **`/admin`** on your laptop — the *Local admin* URL. You drive from here.
- **`/present`** on the projector — QR code, questions, results.

Open **Present (drive)** instead of plain `/present` and the shortcuts work
from the projector too, so you never switch windows.

Flags: `--no-tunnel` (local only), `--port`, `--host`, `--domain`.

## Saving a quiz to a file

A quiz can be saved on its own, apart from any session:

```python
quiz.save("quizzes/my_quiz.lykkepoll")
```

That writes a `.lykkepoll` file: plain JSON holding the title, the theme and
the questions in order. Run it without Python in the picture:

```bash
uv run lykkepoller run --file quizzes/my_quiz.lykkepoll
uv run lykkepoller validate quizzes/my_quiz.lykkepoll   # check it first
```

`quizzes/example_quiz.lykkepoll` is the example quiz saved this way. Here is
the shape:

```json
{
  "schema_version": 1,
  "title": "My talk",
  "theme": "notebook",
  "questions": [
    {"type": "multiple_choice", "id": "q1", "prompt": "Pick one",
     "options": [{"id": "A", "label": "Red",  "is_correct": false},
                 {"id": "B", "label": "Blue", "is_correct": true}]},
    {"type": "rating", "id": "q2", "prompt": "How was it?",
     "steps": 5, "low_label": "Bad", "high_label": "Good"},
    {"type": "free_text", "id": "q3", "prompt": "Anything to add?"}
  ]
}
```

Nothing in it is Python, so you can write one by hand, generate it from
another language, or keep it in git next to your slides. Three things the
format does on purpose:

- **Every id is written down.** Question ids and option ids are what answers
  are stored against and what the CSV shows. If the file left them out and we
  invented them on load, inserting a question would silently re-point every
  answer after it.
- **Nothing is left out for being false.** `is_correct` is written on every
  option. A reader in another language should not have to know that a missing
  key means false.
- **An unknown field is an error, not a shrug.** Write `"lowlabel"` and the
  file is refused, naming the line. A typo you find on stage is worse than one
  you find on load. `schema_version` is how the format grows instead.

Files are checked field by field when they load, and every problem is listed
at once:

```
my_quiz.lykkepoll is not a valid quiz file:
  title: String should have at least 1 character
  theme: Input should be 'plain', 'teal', 'editorial', 'dark' or 'notebook'
  questions[0].low_label: Field required
  questions[0].lowlabel: Extra inputs are not permitted
```

`uv run lykkepoller schema` prints the whole format as JSON Schema, if you
want your editor to autocomplete it or another language to generate it.

Python stays the nicer way to *write* a quiz — `correct="Blue"` is friendlier
than spelling out option ids, and the errors point at your line. There is no
loader back into `Quiz` objects: a saved file may carry option ids those
classes would re-letter, and a round trip that quietly changes what the CSV
says is worse than not having one.

## During the talk

| key            | what happens                         |
|----------------|--------------------------------------|
| `→` or `Space` | open the next question               |
| `←`            | go back                              |
| `Esc`          | close the current question           |
| `R`            | show or hide results on the projector|
| `C`            | show which answer was correct        |
| `A`            | approve every free-text answer you haven't crossed out |
| `E`            | end the session (`/admin` only)      |

**Multiple choice and rating** stay hidden until you press `R`. Until then the
room sees the question and a count going up, so you can ask, let them answer,
talk for a minute, then reveal. `C` turns the correct options green and dims the
rest; it resets when the next question opens, so `→` never leaks the next answer.

**Free text** is the other way round. Its panel is already showing when the
question opens, because it opens empty — approving is the gate, not `R`.

Read the answers on `/admin`, click `✕` beside anything you don't want, then
press `A` to approve everything still standing. `A` skips what you crossed out,
so you can press it again as new answers come in. `↺` puts one back.

Approved answers go up as cards, and the cards shrink as they multiply — full
size up to four, down to about half by twenty — so a lively question still fits
on one screen.

Shortcuts are ignored while you are typing in a text field. Each key submits a
form, so the server stays in charge.

## Afterwards

Click **Download CSV** on `/admin`, or:

```bash
uv run lykkepoller export data/<file>.sqlite -o responses.csv
```

One row per answer: `session_id, question_id, question_type, prompt,
participant_id, answer, answer_label, created_at`.

`uv run lykkepoller inspect data/<file>.sqlite` summarises a file without
opening it.

## Reopening a session

Each session is its own SQLite file, named by date and quiz file:

```bash
uv run lykkepoller run --db data/2026-08-22-my_quiz-blue-otter-4281.sqlite
```

(`--db` reopens a session, answers and all. `--file` starts a new one from a
saved quiz. Same command, two different jobs.)

Everything comes back: id, title, admin token (so a bookmarked admin URL still
works), questions and answers.

The quiz file is not read again. What was asked is what was asked — that is why
editing a question mid-talk changes nothing. To use an edited file, start a new
session.

## Letting phones in

`quiz.run()` starts a Cloudflare Quick Tunnel and prints the public URL when it
comes up. The QR code and the join URL follow it automatically.

With your own domain on Cloudflare and a
[named tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/)
routed to it, you get a stable address:

```bash
uv run quizzes/my_quiz.py --domain lykkepoller.com
```

If the address looks wrong, `/admin` shows where it came from (`tunnel`,
`headers`, `override`, `localhost`) and has a field to type one in by hand.

**The QR points at 127.0.0.1.** The tunnel is not up yet. `/present` swaps in
the real QR by itself within a second or two of the `Tunnel:` line appearing —
you do not need to reload it.

## The admin token

The admin URL carries a token. The first visit checks it, moves it into a
cookie, and redirects to a clean `/admin` so it is gone from the address bar
before you share your screen.

This stops a curious audience member clicking your buttons. It is not real
security.

## How it stays live

Phones poll `/api/participant/state/<session-id>` about once a second. That
request is also the heartbeat, so the connected count is just the phones seen in
the last 30 seconds. When the question changes the page reloads and the server
renders the new state — there is no client-side templating anywhere.

## Tests

```bash
uv run pytest
uv run ruff check .
```

`tests/test_db.py` is thick on purpose: the database is the only source of truth
for session state, and it is written in plain SQL. `tests/test_app.py` drives
the whole thing over HTTP. `tests/test_quiz.py` covers what a bad quiz file is
allowed to do, and `tests/test_spec.py` the same for `.lykkepoll` files.

## Layout

```text
src/lykkepoller/
  quiz.py      Quiz, MultipleChoice, Rating, FreeText — what you import
  spec.py      the .lykkepoll file format, and the checks on it
  serve.py     makes the session file, starts the tunnel and the server
  app.py       routes and session state
  db.py        all the SQL
  cli.py       run a saved quiz / reopen / inspect / export
  exports.py   CSV
  qr.py        QR codes
  templates/   base, participant, admin, present
  static/      style.css, app.js, themes/
quizzes/       your quiz files, .py or .lykkepoll
data/          one .sqlite and one .qr.png per session
tests/
```

## License

See `LICENSE`.
