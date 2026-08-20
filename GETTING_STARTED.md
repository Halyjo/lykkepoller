# Getting started

Live polling for your talk. You run it on your laptop, the audience
answers on their phones, the results go up on the projector.

## 1. Install

You need [`uv`](https://docs.astral.sh/uv/) and
[`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/):

```bash
brew install uv cloudflared
uv sync
```

## 2. Write your questions

Copy `questions/demo_questions.yaml` and edit it:

```yaml
title: My talk

questions:
  - id: q1
    type: multiple_choice
    prompt: "Which one is worse?"
    options:
      - {id: A, label: "A missed detection"}
      - {id: B, label: "A false alarm", is_correct: true}

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

- **multiple_choice** — buttons. `is_correct` is optional.
- **free_text** — they type. You pick what reaches the projector.
- **rating** — a scale from 1 to `steps`.

Every question needs its own `id`. Answers are stored under it, so leave
it alone once you have started.

## 3. Start it

```bash
uv run lykkepoller run questions/my_talk.yaml
```

It prints some URLs, then a `Tunnel:` line a moment later. Open two
windows:

- **`/admin`** on your laptop — use the *Local admin* URL. You drive
  everything from here.
- **`/present`** on the projector — the QR code and the results.

The audience scans the QR. It points at the tunnel, so any phone can
reach you.

## 4. During the talk

| key            | what happens                         |
|----------------|--------------------------------------|
| `→` or `Space` | open the next question               |
| `←`            | go back                              |
| `R`            | show the results on the projector    |
| `C`            | show which answer was correct        |
| `A`            | approve all free-text answers so far |
| `Esc`          | close the question                   |
| `E`            | end the session                      |

No result reaches the projector until you press `R` — until then the
audience sees the question and a count going up. So you can ask, let them
answer, talk for a minute, and reveal when you want.

Free text has a second lock: you read the answers on `/admin` and approve
the ones you like. `A` approves them all at once.

## 5. Afterwards

Click **Download CSV** on `/admin`. One row per answer.

The session is saved in `data/`, one file per talk.

## If something goes wrong

**The QR points at 127.0.0.1.** The tunnel is not up yet. Wait for the
`Tunnel:` line, then reload `/present`.

**No `cloudflared`.** Add `--no-tunnel`. Everything works, but only on
your own laptop.

**You closed the terminal.** Start it again on the saved file:

```bash
uv run lykkepoller run --db data/2026-05-02-my_talk-blue-otter-4281.sqlite
```

The answers and the question you were on all come back. The tunnel gets a
new address though, so the room has to scan the QR again.

**You want to change a question mid-talk.** You can't — the answers are
tied to what was asked. Edit the file and start a new session.

---

Everything else: [README](README.md).
