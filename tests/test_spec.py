"""The `.lykkepoll` file format: what a saved quiz is allowed to be.

These files come from outside -- another language, a generator, a text
editor -- so the job here is the opposite of test_quiz.py's. There the
question is "did the author get a friendly error"; here it is "does a bad
file get rejected at all, completely, before a session starts".
"""

import json

import pytest

from lykkepoller import FreeText, MultipleChoice, Quiz, Rating
from lykkepoller import spec as spec_mod
from lykkepoller.spec import SCHEMA_VERSION, QuizFileError, QuizSpec


def good(**over) -> dict:
    """A valid file as a dict, with fields swapped in by keyword."""
    data = {
        "schema_version": SCHEMA_VERSION,
        "title": "T",
        "theme": "plain",
        "questions": [
            {"type": "multiple_choice", "id": "q1", "prompt": "Pick",
             "options": [{"id": "A", "label": "Red", "is_correct": False},
                         {"id": "B", "label": "Blue", "is_correct": True}]},
            {"type": "rating", "id": "q2", "prompt": "How was it?",
             "steps": 5, "low_label": "Bad", "high_label": "Good"},
            {"type": "free_text", "id": "q3", "prompt": "More?"},
        ],
    }
    data.update(over)
    return data


def loads(data: dict) -> QuizSpec:
    return spec_mod.loads(json.dumps(data))


def rejects(data: dict, match: str):
    with pytest.raises(QuizFileError, match=match):
        loads(data)


# --- what a good file gives you -----------------------------------------------


def test_a_good_file_loads():
    quiz = loads(good())
    assert (quiz.title, quiz.theme, len(quiz.questions)) == ("T", "plain", 3)


def test_theme_defaults_to_plain():
    data = good()
    del data["theme"]
    assert loads(data).theme == "plain"


def test_schema_version_defaults_to_the_current_one():
    data = good()
    del data["schema_version"]
    assert loads(data).schema_version == SCHEMA_VERSION


def test_rating_steps_default_to_five():
    data = good(questions=[{"type": "rating", "id": "q1", "prompt": "p",
                            "low_label": "a", "high_label": "b"}])
    assert loads(data).questions[0].steps == 5


def test_whitespace_is_stripped():
    data = good(title="  T  ")
    assert loads(data).title == "T"


# --- the version gate ---------------------------------------------------------


@pytest.mark.parametrize("version", [0, 2, 99])
def test_a_version_we_do_not_read_is_refused(version):
    rejects(good(schema_version=version), "different version of lykkepoller")


# --- what a bad file may not do -----------------------------------------------


def test_not_json(tmp_path):
    p = tmp_path / "bad.lykkepoll"
    p.write_text("{ nope")
    with pytest.raises(QuizFileError, match="not valid JSON"):
        spec_mod.load(p)


def test_json_but_not_an_object():
    with pytest.raises(QuizFileError, match="must hold a JSON object"):
        spec_mod.loads("[1, 2, 3]")


def test_missing_file(tmp_path):
    with pytest.raises(QuizFileError):
        spec_mod.load(tmp_path / "nothing-here.lykkepoll")


def test_no_questions():
    rejects(good(questions=[]), "at least 1 item")


def test_empty_title():
    rejects(good(title="   "), "title")


def test_unknown_theme():
    rejects(good(theme="chartreuse"), "theme")


def test_unknown_question_type():
    rejects(good(questions=[{"type": "riddle", "id": "q1", "prompt": "p"}]),
            "does not match any of the expected tags")


def test_a_typo_in_a_field_name_is_an_error_not_a_shrug():
    # "lowlabel" silently ignored would show up as a blank projector label
    # halfway through a talk. Reject the file instead.
    rejects(
        good(questions=[{"type": "rating", "id": "q1", "prompt": "p",
                         "lowlabel": "a", "high_label": "b"}]),
        "Extra inputs are not permitted",
    )


def test_missing_required_field_names_it():
    rejects(good(questions=[{"type": "free_text", "id": "q1"}]),
            r"questions\[0\].prompt: Field required")


def test_duplicate_question_ids():
    rejects(
        good(questions=[{"type": "free_text", "id": "x", "prompt": "a"},
                        {"type": "free_text", "id": "x", "prompt": "b"}]),
        "question id 'x' twice",
    )


def test_duplicate_option_ids():
    rejects(
        good(questions=[{"type": "multiple_choice", "id": "q1", "prompt": "p",
                         "options": [{"id": "A", "label": "Red"},
                                     {"id": "A", "label": "Blue"}]}]),
        "option id 'A' twice",
    )


def test_duplicate_option_labels():
    rejects(
        good(questions=[{"type": "multiple_choice", "id": "q1", "prompt": "p",
                         "options": [{"id": "A", "label": "Red"},
                                     {"id": "B", "label": "Red"}]}]),
        "option 'Red' twice",
    )


def test_one_option_is_not_a_question():
    rejects(
        good(questions=[{"type": "multiple_choice", "id": "q1", "prompt": "p",
                         "options": [{"id": "A", "label": "Red"}]}]),
        "at least 2 items",
    )


def test_too_many_options():
    rejects(
        good(questions=[{"type": "multiple_choice", "id": "q1", "prompt": "p",
                         "options": [{"id": str(i), "label": f"o{i}"} for i in range(27)]}]),
        "at most 26 items",
    )


@pytest.mark.parametrize("steps", [1, 0, -3, 12, 99])
def test_rating_steps_out_of_range(steps):
    rejects(good(questions=[{"type": "rating", "id": "q1", "prompt": "p", "steps": steps,
                             "low_label": "a", "high_label": "b"}]),
            "steps")


def test_every_problem_is_listed_not_just_the_first():
    with pytest.raises(QuizFileError) as e:
        loads(good(title="", theme="chartreuse"))
    assert "title" in str(e.value) and "theme" in str(e.value)


# --- the round trip -----------------------------------------------------------


def python_quiz() -> Quiz:
    return Quiz(
        title="My talk",
        theme="notebook",
        questions=[
            MultipleChoice("Pick", options=["Red", "Blue"], correct="Blue"),
            Rating("How was it?", low="Bad", high="Good", steps=7),
            FreeText("More?", id="feedback"),
        ],
    )


def test_save_then_load_gives_the_same_quiz(tmp_path):
    path = python_quiz().save(tmp_path / "my.lykkepoll")
    assert spec_mod.load(path) == python_quiz().to_spec()


def test_saved_file_is_readable_json_with_a_version(tmp_path):
    path = python_quiz().save(tmp_path / "my.lykkepoll")
    data = json.loads(path.read_text())
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["title"] == "My talk"
    assert [q["id"] for q in data["questions"]] == ["q1", "q2", "feedback"]
    assert path.read_text().endswith("\n")


def test_save_creates_the_directory(tmp_path):
    path = python_quiz().save(tmp_path / "deep" / "down" / "my.lykkepoll")
    assert path.exists()


def test_the_file_and_the_session_snapshot_are_the_same_shape(tmp_path):
    quiz = python_quiz()
    path = quiz.save(tmp_path / "my.lykkepoll")
    assert spec_mod.load(path).to_questions() == quiz.to_questions()


def test_option_ids_survive_a_file_the_python_api_would_not_have_written():
    # Letters are what Quiz hands out; the format does not insist on them,
    # because the ids in the file are what answers are stored against.
    quiz = loads(good(questions=[{"type": "multiple_choice", "id": "colour", "prompt": "p",
                                  "options": [{"id": "red", "label": "Red"},
                                              {"id": "blue", "label": "Blue"}]}]))
    assert [o["id"] for o in quiz.to_questions()[0]["options"]] == ["red", "blue"]


# --- for other languages ------------------------------------------------------


def test_json_schema_describes_the_file():
    schema = spec_mod.json_schema()
    assert set(schema["required"]) >= {"title", "questions"}
    assert json.dumps(schema)  # no Python objects left in it
