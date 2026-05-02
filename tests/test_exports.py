import csv
import io

from poller import db, exports


def make_qs():
    return [
        {
            "id": "q1",
            "type": "multiple_choice",
            "prompt": "Pick a fruit",
            "options": [{"id": "A", "label": "Apple"}, {"id": "B", "label": "Banana"}],
        },
        {"id": "q2", "type": "free_text", "prompt": "Why?"},
    ]


def session_with_responses(tmp_path):
    p = tmp_path / "s.sqlite"
    c = db.connect(p)
    db.init_schema(c)
    db.create_session(c, "blue-otter-1234", "Demo", make_qs(), "tok")
    db.insert_response(c, "blue-otter-1234", "q1", "p-alice", "A")
    db.insert_response(c, "blue-otter-1234", "q1", "p-bob", "B")
    db.insert_response(c, "blue-otter-1234", "q2", "p-alice", "because reasons")
    return c, db.get_session(c)


def test_csv_header_columns_match_spec():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(exports.CSV_HEADER)
    expected = (
        "session_id,question_id,question_type,prompt,participant_id,answer,answer_label,created_at"
    )
    assert out.getvalue().strip() == expected


def test_csv_empty_session(tmp_path):
    p = tmp_path / "s.sqlite"
    c = db.connect(p)
    db.init_schema(c)
    db.create_session(c, "blue-otter-1234", "Demo", make_qs(), "tok")
    csv_text = exports.csv_for_session(c, db.get_session(c))
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert len(rows) == 1  # header only
    assert rows[0] == exports.CSV_HEADER


def test_csv_mc_answer_label_populated(tmp_path):
    c, sess = session_with_responses(tmp_path)
    csv_text = exports.csv_for_session(c, sess)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    a_row = next(r for r in rows if r["question_id"] == "q1" and r["answer"] == "A")
    assert a_row["answer_label"] == "Apple"
    assert a_row["question_type"] == "multiple_choice"
    assert a_row["prompt"] == "Pick a fruit"


def test_csv_free_text_answer_label_empty(tmp_path):
    c, sess = session_with_responses(tmp_path)
    csv_text = exports.csv_for_session(c, sess)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    ft_row = next(r for r in rows if r["question_id"] == "q2")
    assert ft_row["answer"] == "because reasons"
    assert ft_row["answer_label"] == ""
    assert ft_row["question_type"] == "free_text"


def test_csv_orphan_response_still_exported(tmp_path):
    """Questions removed from the snapshot but with surviving responses still
    appear in CSV (with empty type/prompt/label)."""
    p = tmp_path / "s.sqlite"
    c = db.connect(p)
    db.init_schema(c)
    db.create_session(c, "blue-otter-1234", "Demo", make_qs(), "tok")
    db.insert_response(c, "blue-otter-1234", "q1", "p-alice", "A")
    # Replace snapshot so q1 is gone.
    db.replace_questions(c, "blue-otter-1234", [{"id": "qZ", "type": "free_text", "prompt": "?"}])
    csv_text = exports.csv_for_session(c, db.get_session(c))
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    orphan = next(r for r in rows if r["question_id"] == "q1")
    assert orphan["answer"] == "A"
    assert orphan["question_type"] == ""
    assert orphan["prompt"] == ""
    assert orphan["answer_label"] == ""
