"""YAML question/slide file loading and validation.

Two supported YAML shapes:

  Legacy (questions only):
      title: ...
      questions: [ {id, type, prompt, ...}, ... ]

  Slides (content + questions, ordered):
      title: ...
      slides:
        - content: slides/01-intro.html
        - question: { id: q1, type: multiple_choice, ... }
        - content: slides/02-discussion.html

Both produce the same shape internally:

    {
      "title": str,
      "questions": [ ...question dicts... ],   # for CSV export and results
      "slides":    [ ...slide entries... ],    # the deck spine
    }

A slide entry is one of:

    {"type": "question", "question_id": "q1"}
    {"type": "content",  "source": "slides/01-intro.html", "html": "<rendered html>"}

For legacy YAMLs the slides list is synthesized as one question slide per question.

Light validation only. A typo causing a clear runtime error is acceptable; we
do not build a schema framework around this.
"""

from pathlib import Path

import yaml


def load(path: Path | str) -> dict:
    """Load and validate a YAML file. See module docstring for the two shapes."""
    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("YAML root must be a mapping")
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is required and must be a non-empty string")

    if "slides" in data:
        return _load_slides(data, talk_dir=path.parent)
    return _load_questions_only(data)


def _load_questions_only(data: dict) -> dict:
    """Legacy path: questions: [...] with no slides. Synthesize one question
    slide per question so the rest of the app can speak slides uniformly."""
    qs = data.get("questions")
    _validate_question_list(qs)
    slides = [{"type": "question", "question_id": q["id"]} for q in qs]
    return {"title": data["title"], "questions": qs, "slides": slides}


def _load_slides(data: dict, *, talk_dir: Path) -> dict:
    """New path: slides: [...] interleaving content files and inline questions."""
    if "questions" in data:
        # Don't accept both. With a slides: list, questions are inline under
        # `question:` entries -- a separate top-level questions: would be a
        # second source of truth.
        raise ValueError(
            "YAML has both 'slides' and 'questions'; use slides: with inline 'question:' entries"
        )
    slides_in = data.get("slides")
    if not isinstance(slides_in, list) or not slides_in:
        raise ValueError("slides must be a non-empty list")

    questions: list[dict] = []
    slides: list[dict] = []
    seen_qids: set[str] = set()
    seen_paths: set[str] = set()
    talk_root = talk_dir.resolve()

    for i, entry in enumerate(slides_in):
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError(
                f"slide {i}: must be a mapping with exactly one of 'content' or 'question'"
            )
        if "content" in entry:
            rel = entry["content"]
            if not isinstance(rel, str) or not rel.strip():
                raise ValueError(f"slide {i}: 'content' must be a non-empty path string")
            full = (talk_dir / rel).resolve()
            # Guard against ../ escaping the talk directory.
            try:
                full.relative_to(talk_root)
            except ValueError as e:
                raise ValueError(
                    f"slide {i}: content path {rel!r} escapes the talk directory"
                ) from e
            if not full.exists():
                raise ValueError(f"slide {i}: content file not found: {full}")
            if rel in seen_paths:
                raise ValueError(f"slide {i}: content {rel!r} reused (each slide should be unique)")
            seen_paths.add(rel)
            slides.append({"type": "content", "source": rel, "html": full.read_text()})
        elif "question" in entry:
            q = entry["question"]
            _validate_one_question(q, seen_qids)
            questions.append(q)
            slides.append({"type": "question", "question_id": q["id"]})
        else:
            raise ValueError(f"slide {i}: must have either 'content' or 'question' key")

    return {"title": data["title"], "questions": questions, "slides": slides}


def validate(data) -> None:
    """Validate a legacy {title, questions} mapping. Kept as a public helper
    because tests and callers of load() in legacy mode rely on it."""
    if not isinstance(data, dict):
        raise ValueError("YAML root must be a mapping")
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is required and must be a non-empty string")
    _validate_question_list(data.get("questions"))


def _validate_question_list(qs) -> None:
    if not isinstance(qs, list) or not qs:
        raise ValueError("questions must be a non-empty list")
    seen_qids: set[str] = set()
    for q in qs:
        _validate_one_question(q, seen_qids)


def _validate_one_question(q, seen_qids: set[str]) -> None:
    if not isinstance(q, dict):
        raise ValueError(f"each question must be a mapping, got {q!r}")
    for key in ("id", "type", "prompt"):
        if not q.get(key):
            raise ValueError(f"question missing required field {key!r}: {q!r}")
    qid = q["id"]
    if qid in seen_qids:
        raise ValueError(f"duplicate question id: {qid!r}")
    seen_qids.add(qid)
    qtype = q["type"]
    if qtype not in ("multiple_choice", "free_text", "rating"):
        raise ValueError(
            f"unknown question type {qtype!r} "
            "(use 'multiple_choice', 'free_text', or 'rating')"
        )
    if qtype == "rating":
        _validate_rating(q)
    elif qtype == "multiple_choice":
        options = q.get("options")
        if not isinstance(options, list) or not options:
            raise ValueError(f"multiple_choice question {qid!r} needs non-empty options")
        seen_oids: set[str] = set()
        for o in options:
            if not isinstance(o, dict):
                raise ValueError(f"option in {qid!r} must be a mapping: {o!r}")
            for key in ("id", "label"):
                if not o.get(key):
                    raise ValueError(f"option missing {key!r} in question {qid!r}")
            oid = o["id"]
            if oid in seen_oids:
                raise ValueError(f"duplicate option id {oid!r} in question {qid!r}")
            seen_oids.add(oid)
            if "is_correct" in o and not isinstance(o["is_correct"], bool):
                raise ValueError(f"option {oid!r} in {qid!r}: is_correct must be a boolean")


def _validate_rating(q: dict) -> None:
    """Rating-scale shape: integer steps (>=2) plus two end labels.

    Like MC, one answer per participant. The answer is the chosen step as a
    string ("1".."steps"). End labels anchor the scale on the participant
    page; the steps in between are unlabeled buttons.
    """
    qid = q["id"]
    steps = q.get("steps")
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 2:
        raise ValueError(f"rating {qid!r}: steps must be an integer >= 2")
    if steps > 11:
        raise ValueError(f"rating {qid!r}: steps must be <= 11 (got {steps})")
    for key in ("low_label", "high_label"):
        v = q.get(key)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(
                f"rating {qid!r}: {key!r} is required and must be a non-empty string"
            )


def find_question(qs: list[dict], qid: str) -> dict | None:
    for q in qs:
        if q["id"] == qid:
            return q
    return None


def slide_index_for_question(slides: list[dict], qid: str) -> int | None:
    """Return the index of the slide that activates question `qid`, or None."""
    for i, s in enumerate(slides):
        if s.get("type") == "question" and s.get("question_id") == qid:
            return i
    return None


def option_label(question: dict, option_id: str) -> str:
    for o in question.get("options", []):
        if o["id"] == option_id:
            return o["label"]
    return ""
