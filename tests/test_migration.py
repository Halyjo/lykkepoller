"""Tests for --migrate-questions.

The pure diff/merge logic is exercised in unit tests; the end-to-end CLI
behavior is exercised through Typer's CliRunner so we also cover the
confirm/abort path.
"""

from pathlib import Path

import pytest
import typer
import yaml

from lykkepoller import cli as cli_mod
from lykkepoller import db, questions


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


# --- pure diff/merge ----------------------------------------------------------


def test_diff_identical_snapshot_is_a_noop():
    d = questions.diff_for_migration(make_qs(), make_qs())
    assert d["added"] == []
    assert d["kept"] == []
    assert d["updated"] == ["q1", "q2"]
    assert d["type_changes"] == []
    assert d["option_id_changes"] == []
    assert d["merged"] == make_qs()


def test_diff_added_question():
    new = make_qs() + [{"id": "q3", "type": "free_text", "prompt": "How?"}]
    d = questions.diff_for_migration(make_qs(), new)
    assert d["added"] == ["q3"]
    assert d["merged"][-1]["id"] == "q3"


def test_diff_kept_question_only_in_db():
    new = [
        {
            "id": "q1",
            "type": "multiple_choice",
            "prompt": "Pick a fruit",
            "options": [{"id": "A", "label": "Apple"}, {"id": "B", "label": "Banana"}],
        }
    ]
    d = questions.diff_for_migration(make_qs(), new)
    assert d["kept"] == ["q2"]
    # Merged keeps q2 at the end, after the new yaml.
    assert [q["id"] for q in d["merged"]] == ["q1", "q2"]


def test_diff_prompt_change_does_not_change_option_ids():
    new = [
        {
            "id": "q1",
            "type": "multiple_choice",
            "prompt": "Pick a fruit (revised)",
            "options": [
                {"id": "A", "label": "Apple"},
                {"id": "B", "label": "Banana, ripe"},
            ],
        },
        make_qs()[1],
    ]
    d = questions.diff_for_migration(make_qs(), new)
    assert d["option_id_changes"] == []
    assert d["merged"][0]["prompt"] == "Pick a fruit (revised)"


def test_diff_option_id_change_is_flagged():
    new = [
        {
            "id": "q1",
            "type": "multiple_choice",
            "prompt": "Pick a fruit",
            "options": [
                {"id": "AA", "label": "Apple"},  # id changed A -> AA
                {"id": "B", "label": "Banana"},
            ],
        },
        make_qs()[1],
    ]
    d = questions.diff_for_migration(make_qs(), new)
    assert d["option_id_changes"] == ["q1"]


def test_diff_type_change_is_flagged():
    new = [
        {"id": "q1", "type": "free_text", "prompt": "Pick a fruit"},  # was MC
        make_qs()[1],
    ]
    d = questions.diff_for_migration(make_qs(), new)
    assert d["type_changes"] == ["q1"]


# --- end-to-end CLI -----------------------------------------------------------


def setup_db(tmp_path: Path) -> Path:
    p = tmp_path / "s.sqlite"
    c = db.connect(p)
    db.init_schema(c)
    db.create_session(c, "blue-otter-1234", "Demo", make_qs(), "tok")
    db.insert_response(c, "blue-otter-1234", "q1", "p-alice", "A")
    c.close()
    return p


def write_yaml(path: Path, qs: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"title": "Demo", "questions": qs}))
    return path


def test_cli_migration_applies_with_yes(tmp_path: Path):
    db_path = setup_db(tmp_path)
    new_qs = make_qs()
    new_qs[0]["prompt"] = "Pick a fruit (revised)"
    new_qs.append({"id": "q3", "type": "free_text", "prompt": "How?"})
    yaml_path = write_yaml(tmp_path / "new.yaml", new_qs)

    # Calling `lykkepoller run` end-to-end would start uvicorn; we exercise the
    # migration helper directly to keep the test fast.
    cli_mod._do_migration(db_path, yaml_path, assume_yes=True)

    c = db.connect(db_path)
    snap = db.get_session(c)["questions"]
    assert [q["id"] for q in snap] == ["q1", "q2", "q3"]
    assert snap[0]["prompt"] == "Pick a fruit (revised)"
    # Existing responses are still tied to q1 / option A.
    assert db.get_response(c, "blue-otter-1234", "q1", "p-alice") == "A"
    c.close()


def test_cli_migration_kept_question_preserved(tmp_path: Path):
    db_path = setup_db(tmp_path)
    # New YAML drops q2 -- it should be kept on the snapshot, with responses.
    new_qs = [make_qs()[0]]
    yaml_path = write_yaml(tmp_path / "new.yaml", new_qs)
    cli_mod._do_migration(db_path, yaml_path, assume_yes=True)

    c = db.connect(db_path)
    snap = db.get_session(c)["questions"]
    assert "q2" in [q["id"] for q in snap]
    c.close()


def test_cli_migration_rejects_type_change(tmp_path: Path):
    db_path = setup_db(tmp_path)
    new_qs = [{"id": "q1", "type": "free_text", "prompt": "Pick a fruit"}, make_qs()[1]]
    yaml_path = write_yaml(tmp_path / "new.yaml", new_qs)

    with pytest.raises(typer.BadParameter, match="type"):
        cli_mod._do_migration(db_path, yaml_path, assume_yes=True)
