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
        if qtype not in ("multiple_choice", "free_text", "rating_scale"):
            raise ValueError(
                f"unknown question type {qtype!r} "
                "(use 'multiple_choice', 'free_text', or 'rating_scale')"
            )
        if qtype == "rating_scale":
            _validate_rating_scale(q)
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


def _validate_rating_scale(q: dict) -> None:
    """Rating-scale shape: integer steps (>=2) plus two end labels.

    Like MC, one answer per participant. The answer is the chosen step as a
    string ("1".."steps"). End labels anchor the scale on the participant
    page; the steps in between are unlabeled buttons.
    """
    qid = q["id"]
    steps = q.get("steps")
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 2:
        raise ValueError(f"rating_scale {qid!r}: steps must be an integer >= 2")
    if steps > 11:
        raise ValueError(f"rating_scale {qid!r}: steps must be <= 11 (got {steps})")
    for key in ("low_label", "high_label"):
        v = q.get(key)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(
                f"rating_scale {qid!r}: {key!r} is required and must be a non-empty string"
            )


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


def diff_for_migration(old: list[dict], new: list[dict]) -> dict:
    """Compute the diff between a stored snapshot and a candidate YAML.

    Used by `--migrate-questions`. Migration rules:
      - Match questions by id.
      - id in both: prompt and options are taken from the new YAML.
      - id only in DB: kept (existing responses still appear in CSV / UI).
      - id only in YAML: appended.
      - Changing a question's `type` for an existing id is forbidden -- the
        caller must surface this and refuse to write.
      - Changing option ids on an MC question orphans existing responses; the
        caller surfaces this as a warning, but the migration still proceeds
        if confirmed.

    Returns a dict with keys:
      added, kept, updated, type_changes, option_id_changes -- lists of qids
      merged -- the new snapshot to persist (only valid if type_changes is empty)
    """
    old_by_id = {q["id"]: q for q in old}
    new_by_id = {q["id"]: q for q in new}

    added = [qid for qid in new_by_id if qid not in old_by_id]
    kept = [qid for qid in old_by_id if qid not in new_by_id]
    updated = [qid for qid in new_by_id if qid in old_by_id]
    type_changes = [qid for qid in updated if old_by_id[qid]["type"] != new_by_id[qid]["type"]]
    option_id_changes = []
    for qid in updated:
        if qid in type_changes:
            continue
        if old_by_id[qid]["type"] == "multiple_choice":
            old_opts = {o["id"] for o in old_by_id[qid].get("options", [])}
            new_opts = {o["id"] for o in new_by_id[qid].get("options", [])}
            if old_opts != new_opts:
                option_id_changes.append(qid)

    merged: list[dict] = list(new)
    seen = {q["id"] for q in new}
    for q in old:
        if q["id"] not in seen:
            merged.append(q)
    return {
        "added": added,
        "kept": kept,
        "updated": updated,
        "type_changes": type_changes,
        "option_id_changes": option_id_changes,
        "merged": merged,
    }
