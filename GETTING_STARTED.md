# Getting started

Live polling for your talk. You run it on your own laptop, the audience
answers on their phones, the results go up on the projector.

Nobody signs up for anything. Nothing leaves your machine except through
the tunnel, and the tunnel closes when you stop the app.

## 1. Install

You need [`uv`](https://docs.astral.sh/uv/) and
[`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
On a Mac:

```bash
brew install uv cloudflared
```

Then, in the project folder:

```bash
uv sync
```

## 2. Write your questions

One file, one talk. Copy `questions/demo_questions.yaml` and edit it:

```yaml
title: My talk

questions:
  - id: q1
    type: multiple_choice
    prompt: "Which one is worse?"
    options:
      - {id: A, label: "A missed detection"}
      - {id: B, label: "A false alarm"}

  - id: q2
    type: free_text
    prompt: "What would you check first?"

  - id: q3
    type: rating
    prompt: "How well did you follow that?"
    steps: 5
    low_label: "Lost"
    high_label: "Got it"
```

Three kinds of question:

- **multiple_choice** — buttons. Add `is_correct: true` to an option if
  there is a right answer.
- **free_text** — they type. You decide which answers reach the projector.
- **rating** — a 1-to-N scale. `steps` is how many buttons.

Give every question an `id`. Any short name works; it just has to be
unique. Answers are stored under it, so leave it alone once you have
started.

## 3. Start it

```bash
uv run lykkepoller run questions/my_talk.yaml
```

It prints a handful of URLs and, a moment later, a `Tunnel:` line:

```
Local admin:      http://127.0.0.1:8000/admin?token=winter-fox-1934
Present:          http://127.0.0.1:8000/present
...
Tunnel:                  https://something.trycloudflare.com
```

You need two windows:

- **`/admin`** on your laptop — open the *Local admin* URL. This is where
  you drive everything.
- **`/present`** on the projector — the big QR code and the results.

The audience scans the QR. It points at the tunnel, so their phones can
reach you from anywhere.

## 4. During the talk

Everything is a key press on `/admin`:

| key            | what happens                              |
|----------------|-------------------------------------------|
| `→` or `Space` | open the next question                    |
| `←`            | go back                                   |
| `R`            | show the results on the projector         |
| `C`            | show which answer was correct             |
| `A`            | approve all free-text answers so far      |
| `Esc`          | close the question                        |
| `E`            | end the session                           |

No result is on the projector until you press `R`. The audience sees the
question and a count going up, and that is all — so you can ask, let them
answer, talk for a minute, and only then reveal.

**Free text is double-locked.** You read the answers on `/admin` and
approve the ones you want. `R` shows only the approved ones. `A` approves
everything at once if you trust the room.

## 5. Afterwards

Click **Download CSV** on `/admin`. One row per answer, ready for a
spreadsheet.

The whole session is also saved in `data/`, one file per talk. Nothing is
lost if your laptop sleeps.

## If something goes wrong

**The QR points at 127.0.0.1.** The tunnel is not up yet. Wait for the
`Tunnel:` line, then reload `/present`.

**No `cloudflared`.** Add `--no-tunnel` to test on your own machine. The
QR only works on your laptop then.

**You closed the terminal.** Start it again pointing at the saved file:

```bash
uv run lykkepoller run --db data/2026-05-02-my_talk-blue-otter-4281.sqlite
```

Everything comes back, including the answers and the question you were on.

**You want to change a question mid-talk.** You can't, and that is on
purpose — the answers are tied to what was asked. Edit the YAML and start
a new session.

---

Everything else — your own domain, slide decks, the internals: [README](README.md).
