# Lykkepoller

Live polling for talks and teaching, run from your own laptop.

You make a quiz. The audience scans a QR code. You open one question at a time
and put the answers on the projector. Afterwards you take the answers away as a
CSV.

```text
  make a quiz              run a session                      take the answers
  ───────────              ─────────────                      ────────────────
  my_quiz.py          ──▶  data/2026-08-24-my_quiz-….sqlite  ──▶  responses.csv
  my_quiz.lykkepoll
```

The middle column is the same whatever the left column was. A quiz is a
`.lykkepoll` file — plain JSON — and Python is the pleasant way to write one.
Anything that can produce that file gets run and exported the same way.

- One quiz per file. Write it in Python; your editor checks it as you type.
- `.lykkepoll` is the saved form: versioned JSON, checked when it loads.
- One SQLite file per session, in `data/`.
- Three pages: `/drive` for you, `/present` for the projector, `/join` for phones.
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

This is the friendliest way in and it is not going anywhere. `quiz.run()` starts
a session straight from the Python file, with no `.lykkepoll` file in sight.

## Save it

Add one line and the same quiz exists on its own, apart from any session:

```python
quiz.save("quizzes/my_quiz.lykkepoll")
```

Worth doing when you want the quiz in git next to your slides, when you want to
hand it to someone who does not run Python, or when you want it back next year
without rerunning a script.

## Run it

| what you have             | how you run it                                            |
|---------------------------|-----------------------------------------------------------|
| a Python quiz             | `uv run quizzes/my_quiz.py`                               |
| a saved quiz              | `uv run lykkepoller run --file quizzes/my_quiz.lykkepoll` |
| a session you already ran | `uv run lykkepoller run --db data/<file>.sqlite`           |

The first two start a **new** session. The third reopens an old one, answers and
all — see [Reopening a session](#reopening-a-session).

Whichever you use, it prints some URLs and then a `Tunnel:` line a moment later.
Open two windows:

- **`/drive`** on your laptop — the *Drive (you)* URL. You drive from here.
- **`/present`** on the projector — QR code, questions, results.

Open **Present (drive)** instead of plain `/present` and the shortcuts work
from the projector too, so you never switch windows.

Flags on all three: `--no-tunnel` (local only), `--port`, `--host`, `--domain`.

## During the talk

| key            | what happens                         |
|----------------|--------------------------------------|
| `→` or `Space` | open the next question               |
| `←`            | go back (from the thank-you screen, back to the last question) |
| `Esc`          | close the current question           |
| `R`            | show or hide results on the projector|
| `C`            | show which answer was correct        |
| `A`            | approve every free-text answer you haven't crossed out |
| `E`            | end the session (`/drive` only)      |

`R` and `C` also have buttons, on the question they act on: only the question
that is open has a pair, and the colour tells you what the projector is showing.

**Multiple choice and rating** stay hidden until you press `R`. Until then the
room sees the question and a count going up, so you can ask, let them answer,
talk for a minute, then reveal. `C` turns the correct options green and dims the
rest; it resets when the next question opens, so `→` never leaks the next answer.

**Free text** is the other way round. Its panel is already showing when the
question opens, because it opens empty — approving is the gate, not `R`.

Read the answers on `/drive`, click `✕` beside anything you don't want, then
press `A` to approve everything still standing. `A` skips what you crossed out,
so you can press it again as new answers come in. `↺` puts one back.

Approved answers go up as cards, and the cards shrink as they multiply — full
size up to four, down to about half by twenty — so a lively question still fits
on one screen.

Shortcuts are ignored while you are typing in a text field. Each key submits a
form, so the server stays in charge.

## Afterwards

Click **Download CSV** on `/drive`, or:

```bash
uv run lykkepoller export data/<file>.sqlite -o responses.csv
```

One row per answer: `session_id, question_id, question_type, prompt,
participant_id, answer, answer_label, created_at`. Every session exports the
same way, whether it started from a `.py` file or a `.lykkepoll` one.

`uv run lykkepoller inspect data/<file>.sqlite` summarises a file without
opening it.

## Reopening a session

Each session is its own SQLite file, named by date and quiz:

```bash
uv run lykkepoller run --db data/2026-08-22-my_quiz-blue-otter-4281.sqlite
```

Everything comes back: id, title, drive token (so a bookmarked drive URL still
works), questions and answers.

The quiz is not read again. What was asked is what was asked — that is why
editing a question mid-talk changes nothing. To use an edited quiz, start a new
session.

## The `.lykkepoll` format

`quizzes/example_quiz.lykkepoll` is the example quiz saved. Here is the shape:

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

Nothing in it is Python, so you can write one by hand or generate it from
somewhere else. Three things the format does on purpose:

- **Every id is written down.** Question ids and option ids are what answers
  are stored against and what the CSV shows. If the file left them out and we
  invented them on load, inserting a question would silently re-point every
  answer after it.
- **Nothing is left out for being false.** `is_correct` is written on every
  option. A reader in another language should not have to know that a missing
  key means false.
- **An unknown field is an error, not a shrug.** Write `"lowlabel"` and the
  file is refused, naming the field. A typo you find on stage is worse than one
  you find on load. `schema_version` is how the format grows instead.

Files are checked field by field when they load, and every problem is listed
at once:

```bash
uv run lykkepoller validate quizzes/my_quiz.lykkepoll
```

```
my_quiz.lykkepoll is not a valid quiz file:
  title: String should have at least 1 character
  theme: Input should be 'plain', 'teal', 'editorial', 'dark' or 'notebook'
  questions[0].low_label: Field required
  questions[0].lowlabel: Extra inputs are not permitted
```

`uv run lykkepoller schema` prints the whole format as JSON Schema, if you want
your editor to autocomplete it or another language to generate it.

There is no loader from a file back into `Quiz` objects. A saved file may carry
option ids those classes would re-letter, and a round trip that quietly changes
what the CSV says is worse than not having one. Edit the Python, or edit the
JSON — not one through the other.

## Where this is going

Today there is one comfortable way to write a quiz — Python — and one format
everything runs from. The next step is a second way in: a page in the app itself
for making a quiz, changing one and deleting one, producing the same
`.lykkepoll` files from a browser instead of an editor.

That page does not exist yet. What already holds is the part it needs: the
format is settled and versioned, it is checked on the way in, and `run --file`
does not care who wrote the file. A quiz made in a browser will run and export
exactly like one written in Python.

Working name for the page is `/quizzes` — a plain noun, so it covers making,
changing and deleting without promising only one of them.

It also sits outside the other three. `/join`, `/present` and `/drive` all name
what the person looking at the page is doing, and all three happen during a
talk. `/quizzes` names a thing rather than an act, which is right: it is the
one page you use when no session is running.

## Letting phones in

`quiz.run()` starts a Cloudflare Quick Tunnel and prints the public URL when it
comes up. The QR code and the join URL follow it automatically.

With your own domain on Cloudflare and a
[named tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/)
routed to it, you get a stable address:

```bash
uv run quizzes/my_quiz.py --domain lykkepoller.com
```

If the address looks wrong, `/drive` shows where it came from (`tunnel`,
`headers`, `override`, `localhost`) and has a field to type one in by hand.

**The QR points at 127.0.0.1.** The tunnel is not up yet. `/present` swaps in
the real QR by itself within a second or two of the `Tunnel:` line appearing —
you do not need to reload it.

## The drive token

The drive URL carries a token. The first visit checks it, moves it into a
cookie, and redirects to a clean `/drive` so it is gone from the address bar
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
the whole thing over HTTP. `tests/test_quiz.py` covers what a bad quiz is
allowed to do in Python, and `tests/test_spec.py` the same for `.lykkepoll`
files.

`tools/smoke.sh` starts a real server and plays a whole session through it —
what `pytest` cannot see, because it never starts one. Point it at either kind
of quiz:

```bash
tools/smoke.sh 9001 quizzes/my_quiz.py
tools/smoke.sh 9001 quizzes/my_quiz.lykkepoll
```

## Layout

```text
src/lykkepoller/
  quiz.py      Quiz, MultipleChoice, Rating, FreeText — what you import
  spec.py      the .lykkepoll format, and the checks on it
  serve.py     makes the session file, starts the tunnel and the server
  app.py       routes and session state
  db.py        all the SQL
  cli.py       run a saved quiz / reopen / inspect / export / validate
  exports.py   CSV
  qr.py        QR codes
  templates/   base, participant, drive, present
  static/      style.css, app.js, themes/
quizzes/       your quizzes, .py or .lykkepoll
data/          one .sqlite and one .qr.png per session
tests/
```

## License

See `LICENSE`.
