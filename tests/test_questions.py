import pytest

from poller import questions


def _ok():
    return {
        "title": "Demo",
        "questions": [
            {
                "id": "q1",
                "type": "multiple_choice",
                "prompt": "Pick one",
                "options": [
                    {"id": "A", "label": "Apple"},
                    {"id": "B", "label": "Banana"},
                ],
            },
            {"id": "q2", "type": "free_text", "prompt": "Why?"},
        ],
    }


def test_validate_ok():
    questions.validate(_ok())


def test_validate_missing_title():
    data = _ok()
    data["title"] = ""
    with pytest.raises(ValueError, match="title"):
        questions.validate(data)


def test_validate_no_questions():
    data = _ok()
    data["questions"] = []
    with pytest.raises(ValueError, match="questions"):
        questions.validate(data)


def test_validate_duplicate_qid():
    data = _ok()
    data["questions"][1]["id"] = "q1"
    with pytest.raises(ValueError, match="duplicate question id"):
        questions.validate(data)


def test_validate_unknown_type():
    data = _ok()
    data["questions"][0]["type"] = "numeric"
    with pytest.raises(ValueError, match="unknown question type"):
        questions.validate(data)


def test_validate_mc_without_options():
    data = _ok()
    data["questions"][0]["options"] = []
    with pytest.raises(ValueError, match="non-empty options"):
        questions.validate(data)


def test_validate_duplicate_option_id():
    data = _ok()
    data["questions"][0]["options"][1]["id"] = "A"
    with pytest.raises(ValueError, match="duplicate option id"):
        questions.validate(data)


def test_validate_option_missing_label():
    data = _ok()
    data["questions"][0]["options"][0]["label"] = ""
    with pytest.raises(ValueError, match="missing 'label'"):
        questions.validate(data)


def test_load_yaml_file(tmp_path):
    f = tmp_path / "q.yaml"
    f.write_text('title: "Hi"\nquestions:\n  - id: q1\n    type: free_text\n    prompt: "Why?"\n')
    data = questions.load(f)
    assert data["title"] == "Hi"
    assert data["questions"][0]["id"] == "q1"


def test_find_question_and_option_label():
    data = _ok()
    q = questions.find_question(data["questions"], "q1")
    assert q["id"] == "q1"
    assert questions.option_label(q, "A") == "Apple"
    assert questions.option_label(q, "missing") == ""
    assert questions.find_question(data["questions"], "missing") is None
