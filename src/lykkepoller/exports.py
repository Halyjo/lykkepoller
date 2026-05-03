"""CSV export for a session."""

import csv
import io
import sqlite3

from . import db, questions

CSV_HEADER = [
    "session_id",
    "question_id",
    "question_type",
    "prompt",
    "participant_id",
    "answer",
    "answer_label",
    "created_at",
]


def csv_for_session(conn: sqlite3.Connection, session: dict) -> str:
    """Return a CSV string with one row per stored response.

    `answer_label` is the human-readable label for multiple-choice answers
    (looked up from the question snapshot) and empty for free-text rows or
    when the option id is no longer present in the snapshot.
    """
    qmap = {q["id"]: q for q in session["questions"]}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_HEADER)
    for r in db.list_all_responses(conn, session["id"]):
        q = qmap.get(r["question_id"])
        qtype = q["type"] if q else ""
        prompt = q["prompt"] if q else ""
        label = ""
        if q is not None and q["type"] == "multiple_choice":
            label = questions.option_label(q, r["answer"])
        writer.writerow(
            [
                session["id"],
                r["question_id"],
                qtype,
                prompt,
                r["participant_id"],
                r["answer"],
                label,
                r["created_at"],
            ]
        )
    return out.getvalue()
