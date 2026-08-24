"""The `.lykkepoll` file format: what a saved quiz looks like on disk.

A `.lykkepoll` file is JSON. It holds a `QuizSpec`: a schema version, the
title, the projector theme, and the questions in the order they are asked.

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

This is the boundary between lykkepoller and everything else. Nothing about
it is Python: write one by hand, generate it from another language, keep it
in git. `uv run lykkepoller validate my.lykkepoll` says whether it is good,
and `uv run lykkepoller run --file my.lykkepoll` runs it.

Three rules the format keeps on purpose:

*Every id is written down.* Question ids and option ids are what answers are
stored against and what the CSV shows. If the file left them out and we made
them up on load, inserting a question would silently re-point every answer
after it. The Python `Quiz` class hands out `q1, q2...` and `A, B, C...` for
you; by the time it reaches a file they are settled.

*Nothing is left out for being false.* `is_correct` is written on every
option, even the wrong ones. A reader in another language should not have to
know that a missing key means false.

*Unknown fields are an error.* A file with `"lowlabel"` in it is a typo, and
a typo you find on stage is worse than one you find on load. `schema_version`
is how the format grows instead.

Validation here is pydantic, because these files come from outside and the
job is to reject a bad one completely. `quiz.py` keeps its own hand-written
checks: those run while you are writing Python, and they can say "Did you
mean 'Blue'?" and point at your line. The two gates suit their two audiences.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

SCHEMA_VERSION = 1

SUFFIX = ".lykkepoll"

Theme = Literal["plain", "teal", "editorial", "dark", "notebook"]
THEMES = ("plain", "teal", "editorial", "dark", "notebook")

MAX_OPTIONS = 26  # options are lettered A..Z
MIN_STEPS, MAX_STEPS = 2, 11  # fewer is not a scale; more is untappable

# Strings that must have something in them. _Strict strips the whitespace
# before the length is measured, so "   " is empty and " Red " is "Red".
Text = Annotated[str, StringConstraints(min_length=1)]


class QuizFileError(ValueError):
    """A `.lykkepoll` file that could not be read, with every problem listed."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Option(_Strict):
    """One button of a multiple-choice question."""

    id: Text
    label: Text
    is_correct: bool = False


class MultipleChoiceSpec(_Strict):
    type: Literal["multiple_choice"]
    id: Text
    prompt: Text
    options: list[Option] = Field(min_length=2, max_length=MAX_OPTIONS)

    @model_validator(mode="after")
    def _no_repeats(self):
        _reject_repeats([o.id for o in self.options], "option id", self.prompt)
        _reject_repeats([o.label for o in self.options], "option", self.prompt)
        return self


class RatingSpec(_Strict):
    type: Literal["rating"]
    id: Text
    prompt: Text
    steps: int = Field(default=5, ge=MIN_STEPS, le=MAX_STEPS)
    low_label: Text
    high_label: Text


class FreeTextSpec(_Strict):
    type: Literal["free_text"]
    id: Text
    prompt: Text


# "type" picks the model, so a bad file is told which kind of question was
# wrong instead of being shown all three sets of errors at once.
QuestionSpec = Annotated[
    MultipleChoiceSpec | RatingSpec | FreeTextSpec,
    Field(discriminator="type"),
]


class QuizSpec(_Strict):
    """A whole saved quiz. This is what a `.lykkepoll` file contains."""

    schema_version: int = SCHEMA_VERSION
    title: Text
    theme: Theme = "plain"
    questions: list[QuestionSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _version_and_ids(self):
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version {self.schema_version} was written by a different "
                f"version of lykkepoller; this one reads {SCHEMA_VERSION}"
            )
        _reject_repeats([q.id for q in self.questions], "question id", self.title)
        return self

    def to_questions(self) -> list[dict]:
        """The question list as plain dicts -- what goes in the session file.

        The runtime reads these dicts and nothing else, so the file format
        and the stored snapshot are the same shape on purpose.
        """
        return [q.model_dump() for q in self.questions]

    def to_json(self) -> str:
        return json.dumps(self.model_dump(), indent=2, ensure_ascii=False) + "\n"

    def save(self, path: str | Path) -> Path:
        """Write the quiz to `path`. Returns the path."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path


def load(path: str | Path) -> QuizSpec:
    """Read a `.lykkepoll` file, checking every field. Raises QuizFileError."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise QuizFileError(f"{path}: {e.strerror or e}") from e
    return loads(raw, where=str(path))


def loads(text: str, *, where: str = "<string>") -> QuizSpec:
    """Same as load(), from a string already in hand."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise QuizFileError(f"{where} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise QuizFileError(f"{where} must hold a JSON object, got {type(data).__name__}")
    try:
        return QuizSpec(**data)
    except ValidationError as e:
        raise QuizFileError(_readable(e, where)) from e


def json_schema() -> dict:
    """The format as JSON Schema, for tools that are not this one."""
    return QuizSpec.model_json_schema()


def _reject_repeats(values: list[str], what: str, where: str) -> None:
    for i, v in enumerate(values):
        if v in values[:i]:
            raise ValueError(f"{where!r} lists the {what} {v!r} twice")


def _readable(error: ValidationError, where: str) -> str:
    """Turn a pydantic report into one line per problem, naming the field.

    Pydantic's own text repeats the model names and the docs URL, which is
    noise when the reader is looking at their own file.
    """
    lines = [f"{where} is not a valid quiz file:"]
    for err in error.errors():
        # ('questions', 2, 'multiple_choice', 'options', 0, 'label') reads
        # better as questions[2].options[0].label; the tag naming which
        # branch of the union matched is ours, not the user's.
        parts = [p for p in err["loc"] if p not in ("multiple_choice", "rating", "free_text")]
        field = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in parts).lstrip(".")
        lines.append(f"  {field or '(whole file)'}: {err['msg']}")
    return "\n".join(lines)


if __name__ == "__main__":
    spec = QuizSpec(
        title="Demo",
        theme="notebook",
        questions=[
            {"type": "multiple_choice", "id": "q1", "prompt": "Why a loss function?",
             "options": [{"id": "A", "label": "To score it"},
                         {"id": "B", "label": "To teach it", "is_correct": True}]},
            {"type": "rating", "id": "q2", "prompt": "Follow that?",
             "low_label": "Lost", "high_label": "Got it"},
            {"type": "free_text", "id": "q3", "prompt": "Anything to add?"},
        ],
    )
    print(spec.to_json())

    for bad in (
        '{"schema_version": 1, "title": "T", "questions": []}',
        '{"schema_version": 1, "title": "T", "questions": [{"type": "riddle", "id": "q1"}]}',
        '{"schema_version": 1, "title": "T", "questions": ['
        '{"type": "rating", "id": "q1", "prompt": "p", "lowlabel": "a", "high_label": "b"}]}',
        '{"schema_version": 99, "title": "T", "questions": ['
        '{"type": "free_text", "id": "q1", "prompt": "p"}]}',
    ):
        try:
            loads(bad, where="<demo>")
        except QuizFileError as e:
            print("caught:", e)
