"""The authoring layer: what a bad quiz file is allowed to do.

Every check here fires while the quiz is being built, which is the whole
point -- the error names the field and points at the user's line.
"""

import pytest

from lykkepoller.quiz import FreeText, MultipleChoice, Quiz, Rating, find_question, option_label


def ok_quiz(**kw):
    kw.setdefault("title", "T")
    kw.setdefault("questions", [FreeText("Say something")])
    return Quiz(**kw)


# --- ids ----------------------------------------------------------------------


def test_ids_are_positional():
    q = ok_quiz(questions=[FreeText("a"), FreeText("b"), FreeText("c")])
    assert [x["id"] for x in q.to_questions()] == ["q1", "q2", "q3"]


def test_explicit_id_wins():
    q = ok_quiz(questions=[FreeText("a", id="intro"), FreeText("b")])
    assert [x["id"] for x in q.to_questions()] == ["intro", "q2"]


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="share the id 'x'"):
        ok_quiz(questions=[FreeText("a", id="x"), FreeText("b", id="x")])


def test_option_ids_are_letters():
    q = ok_quiz(questions=[MultipleChoice("p", options=["a", "b", "c"])])
    assert [o["id"] for o in q.to_questions()[0]["options"]] == ["A", "B", "C"]


# --- multiple choice ----------------------------------------------------------


def test_correct_marks_the_option():
    q = ok_quiz(questions=[MultipleChoice("p", options=["a", "b"], correct="b")])
    opts = q.to_questions()[0]["options"]
    # Written on every option, not only the right one: a reader in another
    # language should not have to know a missing key means false.
    assert opts[0]["is_correct"] is False
    assert opts[1]["is_correct"] is True


def test_several_correct_options():
    q = ok_quiz(questions=[MultipleChoice("p", options=["a", "b", "c"], correct=["a", "c"])])
    marks = [o["is_correct"] for o in q.to_questions()[0]["options"]]
    assert marks == [True, False, True]


def test_no_correct_marks_nothing():
    q = ok_quiz(questions=[MultipleChoice("p", options=["a", "b"])])
    assert all(o["is_correct"] is False for o in q.to_questions()[0]["options"])


def test_correct_typo_suggests_the_real_option():
    with pytest.raises(ValueError, match="Did you mean 'Blue'"):
        MultipleChoice("p", options=["Red", "Blue"], correct="Blu")


def test_correct_with_no_near_match_still_fails():
    with pytest.raises(ValueError, match="is not an option"):
        MultipleChoice("p", options=["Red", "Blue"], correct="zzzzzz")


def test_one_option_rejected():
    with pytest.raises(ValueError, match="at least two options"):
        MultipleChoice("p", options=["only"])


def test_duplicate_options_rejected():
    with pytest.raises(ValueError, match="lists 'Red' twice"):
        MultipleChoice("p", options=["Red", "Blue", "Red"])


def test_too_many_options_rejected():
    with pytest.raises(ValueError, match="the most is 26"):
        MultipleChoice("p", options=[str(i) for i in range(27)])


def test_empty_option_rejected():
    with pytest.raises(ValueError, match="option 2"):
        MultipleChoice("p", options=["a", "  "])


def test_options_must_be_a_list():
    with pytest.raises(ValueError, match="must be a list"):
        MultipleChoice("p", options="Red")


# --- rating -------------------------------------------------------------------


def test_rating_defaults_to_five_steps():
    q = ok_quiz(questions=[Rating("p", low="l", high="h")])
    assert q.to_questions()[0]["steps"] == 5


def test_rating_end_labels_land_in_the_snapshot():
    q = ok_quiz(questions=[Rating("p", low="Lost", high="Got it")])
    d = q.to_questions()[0]
    assert (d["low_label"], d["high_label"]) == ("Lost", "Got it")


@pytest.mark.parametrize("steps", [1, 0, -3, 12, 99])
def test_rating_steps_out_of_range(steps):
    with pytest.raises(ValueError, match="must be 2-11"):
        Rating("p", low="l", high="h", steps=steps)


@pytest.mark.parametrize("steps", ["5", 5.0, True, None])
def test_rating_steps_must_be_a_whole_number(steps):
    with pytest.raises(ValueError, match="whole number"):
        Rating("p", low="l", high="h", steps=steps)


def test_rating_needs_end_labels():
    with pytest.raises(ValueError, match="low="):
        Rating("p", low="", high="h")


# --- quiz ---------------------------------------------------------------------


def test_empty_prompt_rejected():
    with pytest.raises(ValueError, match="prompt"):
        FreeText("   ")


def test_prompt_is_stripped():
    assert FreeText("  hello  ").prompt == "hello"


def test_title_required():
    with pytest.raises(ValueError, match="title"):
        ok_quiz(title="")


def test_at_least_one_question():
    with pytest.raises(ValueError, match="at least one question"):
        ok_quiz(questions=[])


def test_non_question_in_the_list():
    with pytest.raises(ValueError, match="is a str"):
        ok_quiz(questions=["not a question"])


def test_unknown_theme_lists_the_real_ones():
    with pytest.raises(ValueError, match="notebook"):
        ok_quiz(theme="chartreuse")


def test_known_theme_accepted():
    assert ok_quiz(theme="dark").theme == "dark"


# --- reading a snapshot back --------------------------------------------------


def test_find_question_and_option_label():
    qs = ok_quiz(questions=[MultipleChoice("p", options=["Red", "Blue"])]).to_questions()
    assert find_question(qs, "q1")["prompt"] == "p"
    assert find_question(qs, "nope") is None
    assert option_label(qs[0], "B") == "Blue"
    assert option_label(qs[0], "Z") == ""
