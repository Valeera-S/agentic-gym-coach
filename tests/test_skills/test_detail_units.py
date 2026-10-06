"""coach_session_detail: unit_as_entered is null when no weight was entered (P61)."""

from __future__ import annotations

from coach_tools import DISPATCH
from models import ExerciseDetail


def _detail(exercises: list[dict]) -> list[dict]:
    sid = DISPATCH["log_session"]({"date": "2026-10-01", "exercises": exercises})["session_id"]
    return DISPATCH["session_detail"]({"session_id": sid})["sessions"][0]["exercises"]


def test_a_bodyweight_row_has_no_unit_as_entered():
    pullup, = _detail([{"name": "Pull-Up", "sets": 2, "reps": [8, 7]}])
    assert pullup["weight_as_entered"] == [None, None]
    assert pullup["unit_as_entered"] is None


def test_a_row_with_only_null_weights_has_no_unit_as_entered():
    pullup, = _detail([{"name": "Pull-Up", "sets": 2, "weight_kg": [None, None]}])
    assert pullup["unit_as_entered"] is None


def test_kg_and_lb_rows_keep_their_unit():
    bench, fly, nothing = _detail([
        {"name": "Bench Press", "sets": 1, "weight_kg": [80]},
        {"name": "Dumbbell Fly", "sets": 1, "weight": [15], "unit": "lb"},
        {"name": "Pull-Up", "sets": 1}])
    assert bench["unit_as_entered"] == "kg"
    assert fly["unit_as_entered"] == "lb"
    assert nothing["unit_as_entered"] is None


def test_a_mixed_row_with_some_weights_keeps_the_unit():
    ex, = _detail([{"name": "Dip", "sets": 2, "weight_kg": [None, 10]}])
    assert ex["unit_as_entered"] == "kg"


def test_the_two_field_pairs_are_both_still_there_and_documented():
    fields = ExerciseDetail.model_fields
    for name in ("weight_as_entered", "unit_as_entered", "entered_weight", "entered_unit"):
        assert name in fields
    import mcp_server
    doc = mcp_server.coach_session_detail.__doc__
    assert "entered_weight" in doc and "falls back to kg" in doc
    from pathlib import Path
    prompt = (Path(__file__).resolve().parents[2] / "docs" / "COACH_PROMPT.md").read_text(encoding="utf-8")
    assert "entered_weight" in prompt and "falls back to kg" in prompt
