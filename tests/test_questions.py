import pytest

from lykkepoller import questions


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


def test_validate_is_correct_optional():
    data = _ok()
    data["questions"][0]["options"][0]["is_correct"] = True
    data["questions"][0]["options"][1]["is_correct"] = False
    questions.validate(data)  # both bool values, including absence on others, are fine


def test_validate_is_correct_must_be_bool():
    data = _ok()
    data["questions"][0]["options"][0]["is_correct"] = "yes"
    with pytest.raises(ValueError, match="is_correct must be a boolean"):
        questions.validate(data)


def _ok_rating():
    return {
        "id": "qr",
        "type": "rating",
        "prompt": "How was it?",
        "steps": 5,
        "low_label": "Bad",
        "high_label": "Good",
    }


def test_validate_rating_ok():
    questions.validate({"title": "T", "questions": [_ok_rating()]})


def test_validate_rating_requires_steps_int():
    q = _ok_rating()
    q["steps"] = "5"
    with pytest.raises(ValueError, match="steps must be an integer"):
        questions.validate({"title": "T", "questions": [q]})


def test_validate_rating_rejects_too_few_steps():
    q = _ok_rating()
    q["steps"] = 1
    with pytest.raises(ValueError, match="steps must be an integer >= 2"):
        questions.validate({"title": "T", "questions": [q]})


def test_validate_rating_rejects_too_many_steps():
    q = _ok_rating()
    q["steps"] = 12
    with pytest.raises(ValueError, match="steps must be <= 11"):
        questions.validate({"title": "T", "questions": [q]})


def test_validate_rating_requires_end_labels():
    q = _ok_rating()
    q["low_label"] = ""
    with pytest.raises(ValueError, match="low_label"):
        questions.validate({"title": "T", "questions": [q]})


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



# --- slides: YAML shape ----------------------------------------------------


def _write_yaml(tmp_path, body):
    p = tmp_path / "talk.yaml"
    p.write_text(body)
    return p


def test_legacy_questions_only_synthesizes_slides(tmp_path):
    """A YAML without `slides:` still loads and exposes a slides list with
    one question slide per question."""
    p = _write_yaml(tmp_path, """\
title: Demo
questions:
  - id: q1
    type: free_text
    prompt: Why?
  - id: q2
    type: free_text
    prompt: When?
""")
    data = questions.load(p)
    assert [s["type"] for s in data["slides"]] == ["question", "question"]
    assert [s["question_id"] for s in data["slides"]] == ["q1", "q2"]
    # Question list still present so existing call sites keep working.
    assert [q["id"] for q in data["questions"]] == ["q1", "q2"]


def test_slides_yaml_loads_content_and_questions(tmp_path):
    (tmp_path / "intro.html").write_text("<h1>Hello</h1>")
    p = _write_yaml(tmp_path, """\
title: Talk
slides:
  - content: intro.html
  - question:
      id: q1
      type: free_text
      prompt: Why?
""")
    data = questions.load(p)
    assert data["slides"][0] == {
        "type": "content",
        "source": "intro.html",
        "html": "<h1>Hello</h1>",
    }
    assert data["slides"][1] == {"type": "question", "question_id": "q1"}
    assert [q["id"] for q in data["questions"]] == ["q1"]


def test_slides_rejects_questions_and_slides_together(tmp_path):
    (tmp_path / "x.html").write_text("<p>x</p>")
    p = _write_yaml(tmp_path, """\
title: Talk
questions:
  - {id: q1, type: free_text, prompt: "A?"}
slides:
  - content: x.html
""")
    with pytest.raises(ValueError, match="both 'slides' and 'questions'"):
        questions.load(p)


def test_slides_rejects_missing_content_file(tmp_path):
    p = _write_yaml(tmp_path, """\
title: Talk
slides:
  - content: missing.html
""")
    with pytest.raises(ValueError, match="content file not found"):
        questions.load(p)


def test_slides_rejects_path_escaping_talk_dir(tmp_path):
    p = _write_yaml(tmp_path, """\
title: Talk
slides:
  - content: ../escape.html
""")
    with pytest.raises(ValueError, match="escapes the talk directory"):
        questions.load(p)


def test_slide_index_for_question(tmp_path):
    (tmp_path / "intro.html").write_text("<p>intro</p>")
    (tmp_path / "outro.html").write_text("<p>outro</p>")
    p = _write_yaml(tmp_path, """\
title: T
slides:
  - content: intro.html
  - question:
      id: q1
      type: free_text
      prompt: A?
  - content: outro.html
  - question:
      id: q2
      type: free_text
      prompt: B?
""")
    data = questions.load(p)
    assert questions.slide_index_for_question(data["slides"], "q1") == 1
    assert questions.slide_index_for_question(data["slides"], "q2") == 3
    assert questions.slide_index_for_question(data["slides"], "missing") is None
