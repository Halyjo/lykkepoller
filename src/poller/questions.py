"""YAML question file loading and validation.

Light validation only. A typo causing a clear runtime error is acceptable;
we do not build a schema framework around this.
"""

from pathlib import Path

import yaml


def load(path: Path | str) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    validate(data)
    return data


def validate(data) -> None:
    if not isinstance(data, dict):
        raise ValueError("YAML root must be a mapping")
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is required and must be a non-empty string")
    qs = data.get("questions")
    if not isinstance(qs, list) or not qs:
        raise ValueError("questions must be a non-empty list")
    seen_qids: set[str] = set()
    for q in qs:
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
        if qtype not in ("multiple_choice", "free_text"):
            raise ValueError(
                f"unknown question type {qtype!r} (use 'multiple_choice' or 'free_text')"
            )
        if qtype == "multiple_choice":
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


def find_question(qs: list[dict], qid: str) -> dict | None:
    for q in qs:
        if q["id"] == qid:
            return q
    return None


def option_label(question: dict, option_id: str) -> str:
    for o in question.get("options", []):
        if o["id"] == option_id:
            return o["label"]
    return ""
