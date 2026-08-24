"""The quiz you write. Import these four names, build a Quiz, call run().

    from lykkepoller import Quiz, MultipleChoice, Rating, FreeText

    quiz = Quiz(title="My talk", questions=[
        MultipleChoice("Pick one", options=["Red", "Blue"], correct="Blue"),
        Rating("How was it?", low="Bad", high="Good"),
        FreeText("Anything to add?"),
    ])

    if __name__ == "__main__":
        quiz.run()

Everything is checked as the question is built, so mistakes point at your
line. Ids are handed out for you: questions q1, q2..., options A, B, C...
Pass id= to set one yourself -- that is what shows up in the CSV.

These objects exist only until the quiz is handed over. to_spec() turns
them into a QuizSpec -- the checked, versioned shape in spec.py that both
the `.lykkepoll` file format and the session database are written from.
Once a session starts, that snapshot is all anyone reads.

quiz.save("my.lykkepoll") writes the file. There is no load() back into
these classes on purpose: a saved file may carry option ids these classes
would re-letter, so the round trip would quietly change what the CSV says.
Run a saved file with `lykkepoller run --file my.lykkepoll` instead.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The limits and the theme list live in spec.py, so the file format and the
# Python API cannot drift apart on what a valid quiz is.
from .spec import MAX_OPTIONS, MAX_STEPS, MIN_STEPS, THEMES, QuizSpec, Theme


def _text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")
    return value.strip()


@dataclass
class MultipleChoice:
    """Buttons. One answer per person, locked once given.

    correct= is the text of the right answer, or a list of them. Leave it
    out for a poll with no right answer.
    """

    prompt: str
    options: list[str]
    correct: str | list[str] | None = None
    id: str | None = None
    # correct=, checked and always a tuple. to_dict() reads this one.
    right: tuple[str, ...] = field(init=False, default=(), repr=False)

    def __post_init__(self):
        self.prompt = _text(self.prompt, "prompt")
        self.options = _check_options(self.options, self.prompt)
        self.right = _check_correct(self.correct, self.options, self.prompt)

    def to_dict(self) -> dict:
        options = [
            {"id": chr(65 + i), "label": label, "is_correct": label in self.right}
            for i, label in enumerate(self.options)
        ]
        return {"type": "multiple_choice", "id": self.id, "prompt": self.prompt,
                "options": options}


@dataclass
class Rating:
    """Numbered buttons, 1 to steps. Only the ends are labelled, which is
    what keeps "4 out of 5" from being ambiguous -- so both are required."""

    prompt: str
    low: str
    high: str
    steps: int = 5
    id: str | None = None

    def __post_init__(self):
        self.prompt = _text(self.prompt, "prompt")
        self.low = _text(self.low, f"low= for {self.prompt!r}")
        self.high = _text(self.high, f"high= for {self.prompt!r}")
        # bool is an int subclass, so steps=True would otherwise slip past.
        if not isinstance(self.steps, int) or isinstance(self.steps, bool):
            raise ValueError(
                f"steps= for {self.prompt!r} must be a whole number, got {self.steps!r}"
            )
        if not MIN_STEPS <= self.steps <= MAX_STEPS:
            raise ValueError(
                f"steps= for {self.prompt!r} must be {MIN_STEPS}-{MAX_STEPS}, got {self.steps}"
            )

    def to_dict(self) -> dict:
        return {
            "type": "rating", "id": self.id, "prompt": self.prompt,
            "steps": self.steps, "low_label": self.low, "high_label": self.high,
        }


@dataclass
class FreeText:
    """A text box. People may send several answers; you pick which ones
    reach the projector."""

    prompt: str
    id: str | None = None

    def __post_init__(self):
        self.prompt = _text(self.prompt, "prompt")

    def to_dict(self) -> dict:
        return {"type": "free_text", "id": self.id, "prompt": self.prompt}


Question = MultipleChoice | Rating | FreeText


def _check_options(options, prompt: str) -> list[str]:
    if not isinstance(options, (list, tuple)):
        raise ValueError(f"options= for {prompt!r} must be a list of answer texts, got {options!r}")
    clean = [_text(o, f"option {i + 1} of {prompt!r}") for i, o in enumerate(options)]
    if len(clean) < 2:
        raise ValueError(f"{prompt!r} needs at least two options; one answer is not a question.")
    if len(clean) > MAX_OPTIONS:
        raise ValueError(f"{prompt!r} has {len(clean)} options; the most is {MAX_OPTIONS}.")
    for i, o in enumerate(clean):
        if o in clean[:i]:
            raise ValueError(f"{prompt!r} lists {o!r} twice.")
    return clean


def _check_correct(correct, options: list[str], prompt: str) -> tuple[str, ...]:
    if correct is None:
        return ()
    wanted = [correct] if isinstance(correct, str) else list(correct)
    for w in wanted:
        if w not in options:
            near = difflib.get_close_matches(w, options, n=1)
            hint = f" Did you mean {near[0]!r}?" if near else ""
            raise ValueError(f"correct={w!r} is not an option for {prompt!r}.{hint}")
    return tuple(wanted)


@dataclass
class Quiz:
    """A title, some questions, and the projector theme."""

    title: str
    questions: list[Question]
    theme: Theme = "plain"

    def __post_init__(self):
        self.title = _text(self.title, "title")
        if not isinstance(self.questions, (list, tuple)) or not self.questions:
            raise ValueError("questions= must be a list with at least one question")
        self.questions = list(self.questions)
        if self.theme not in THEMES:
            raise ValueError(f"theme={self.theme!r} is not one of: {', '.join(THEMES)}")

        # Ids are positional, so they are handed out here -- a question does
        # not know where in the list it sits.
        seen: dict[str, int] = {}
        for i, q in enumerate(self.questions):
            if not isinstance(q, (MultipleChoice, Rating, FreeText)):
                raise ValueError(
                    f"question {i + 1} is a {type(q).__name__}; must be "
                    "MultipleChoice, Rating or FreeText"
                )
            q.id = f"q{i + 1}" if q.id is None else _text(q.id, f"id= for {q.prompt!r}")
            if q.id in seen:
                raise ValueError(
                    f"questions {seen[q.id] + 1} and {i + 1} share the id {q.id!r}. "
                    "Ids must differ -- answers are stored against them."
                )
            seen[q.id] = i

    def to_spec(self) -> QuizSpec:
        """This quiz as a QuizSpec -- the shape files and sessions are made of."""
        return QuizSpec(
            title=self.title,
            theme=self.theme,
            questions=[q.to_dict() for q in self.questions],
        )

    def to_questions(self) -> list[dict]:
        """The stored snapshot. Goes through the spec, so it is checked twice."""
        return self.to_spec().to_questions()

    def save(self, path: str | Path) -> Path:
        """Write this quiz to a `.lykkepoll` file. Returns the path."""
        return self.to_spec().save(path)

    def run(self, *, host="127.0.0.1", port=8000, tunnel=True, domain=None, tunnel_name=None):
        """Start a session and serve it. Command-line flags win over these,
        so `uv run my_quiz.py --no-tunnel` works without editing the file."""
        from . import serve as serve_mod

        opts = _flags(host, port, tunnel, domain, tunnel_name)
        source = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else slugify(self.title)
        db_path = serve_mod.new_session_db(self.to_spec(), source_name=source)
        serve_mod.serve(db_path=db_path, **opts)


def slugify(text: str) -> str:
    """Filename-safe: keep letters, digits, . - _; collapse the rest to a dash."""
    return re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9._-]+", "-", text)).strip("-_.") or "quiz"


def _flags(host, port, tunnel, domain, tunnel_name) -> dict:
    p = argparse.ArgumentParser(description="Run this quiz. Defaults come from quiz.run(...).")
    p.add_argument("--host", default=host)
    p.add_argument("--port", type=int, default=port)
    p.add_argument("--no-tunnel", action="store_true", help="Local only; skip cloudflared.")
    p.add_argument("--domain", default=domain, help="Advertise https://DOMAIN.")
    p.add_argument("--tunnel-name", default=tunnel_name, help="Named tunnel to run.")
    a = p.parse_args()
    if a.tunnel_name and not a.domain:
        p.error("--tunnel-name needs --domain")
    return {
        "host": a.host, "port": a.port, "tunnel": tunnel and not a.no_tunnel,
        "domain": a.domain, "tunnel_name": a.tunnel_name,
    }


# --- reading a stored session back -------------------------------------------
# These take the dicts from the database, not the dataclasses above.


def find_question(questions: list[dict], qid: str) -> dict | None:
    return next((q for q in questions if q["id"] == qid), None)


def option_label(question: dict, option_id: str) -> str:
    return next((o["label"] for o in question.get("options", []) if o["id"] == option_id), "")


if __name__ == "__main__":
    demo = Quiz(title="Demo", theme="notebook", questions=[
        MultipleChoice("Why a loss function?", options=["To score it", "To teach it"],
                       correct="To teach it"),
        Rating("Follow that?", low="Lost", high="Got it"),
        FreeText("Anything to add?"),
    ])
    for q in demo.to_questions():
        print(q)
    print(demo.to_spec().to_json())

    for bad in (
        lambda: MultipleChoice("Oops", options=["Red", "Blue"], correct="Blu"),
        lambda: Rating("Scale", low="a", high="b", steps=99),
        lambda: Quiz(title="T", questions=[FreeText("A", id="x"), FreeText("B", id="x")]),
    ):
        try:
            bad()
        except ValueError as e:
            print("caught:", e)
