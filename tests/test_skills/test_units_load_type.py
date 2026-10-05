"""Load semantics and units — load_type, weight + unit input, tonnage.

weight_kg stays "the reading on the implement"; load_type says how it was
read; callers may log the gym's own numbers in lb and get the exact kg
conversion, with what they entered kept for read-back.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from coach_tools import DISPATCH
from models import ExerciseModel, LoadType, MuscleGroup, SessionInput
from models.session import LB_TO_KG
from skills.init import get_duckdb
from skills.session_logger import log_session
from skills.trend_analysis import get_specialization_trend

T = date.today() - timedelta(days=1)


def _stored(field: str):
    return get_duckdb().execute(f"SELECT exercises[1].{field} FROM sessions").fetchone()[0]


# --- input forms ------------------------------------------------------------------

def test_lb_entry_stores_entered_values_and_exact_kg():
    log_session(SessionInput(date=T, exercises=[ExerciseModel(
        name="Dumbbell Bench Press", sets=3, reps=[10, 10, 8],
        weight=[25, 25, 22.5], unit="lb", load_type="per_hand")]))
    assert _stored("entered_weight") == [25.0, 25.0, 22.5]
    assert _stored("entered_unit") == "lb"
    assert _stored("weight_kg") == [25 * LB_TO_KG, 25 * LB_TO_KG, 22.5 * LB_TO_KG]
    assert _stored("weight_kg")[0] == 11.33980925  # stored exactly (DOUBLE)
    assert _stored("load_type") == "per_hand"


def test_kg_entry_through_weight_plus_unit():
    log_session(SessionInput(date=T, exercises=[ExerciseModel(
        name="Lat Pulldown", sets=2, reps=[10, 10], weight=[50, 52.5], unit="kg")]))
    assert _stored("weight_kg") == [50.0, 52.5]
    assert (_stored("entered_weight"), _stored("entered_unit")) == ([50.0, 52.5], "kg")


def test_legacy_weight_kg_call_behaves_exactly_as_before():
    log_session(SessionInput(date=T, exercises=[ExerciseModel(
        name="Lat Pulldown", sets=2, reps=[10, 10], weight_kg=[22.68, 22.68])]))
    assert _stored("weight_kg") == [22.68, 22.68]
    assert _stored("entered_weight") is None and _stored("entered_unit") is None
    assert _stored("load_type") is None  # unknown, never guessed


def test_bodyweight_entry_with_null_weights_in_lb():
    e = ExerciseModel(name="Pull-Up", sets=2, reps=[8, 7], weight=[None, None], unit="lb",
                      load_type="bodyweight")
    assert e.weight_kg == [None, None]


@pytest.mark.parametrize("bad", [
    {"weight": [25]},                                   # no unit
    {"unit": "lb", "weight_kg": [11.3]},                # unit without weight
    {"unit": "lb"},                                     # unit alone
    {"weight": [25], "unit": "lb", "weight_kg": [11.3]},  # both forms
    {"weight": [5000], "unit": "lb"},                   # 2268 kg: out of bounds
    {"weight": [-5], "unit": "kg"},
    {"weight": [float("nan")], "unit": "kg"},
    {"weight": [25], "unit": "stone"},
    {"weight": [25], "unit": "LB"},                     # vocabulary is exact
    {"load_type": "per_foot"},
    {"weight": [25, 25], "unit": "lb", "reps": [10]},   # per-set length conflict
])
def test_malformed_unit_input_is_rejected_before_any_write(bad):
    with pytest.raises(ValidationError):
        ExerciseModel(name="x", sets=1, **bad)


def test_malformed_unit_input_via_cli_is_invalid_input_and_writes_nothing():
    from coach_tools import error_payload
    with pytest.raises(ValidationError) as exc:
        DISPATCH["log_session"]({"date": "2026-10-01", "exercises": [
            {"name": "Lat Pulldown", "sets": 1, "weight": [50], "weight_kg": [22.7], "unit": "lb"}]})
    assert error_payload(exc.value)["error"] == "invalid_input"
    assert get_duckdb().execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_mcp_wrapper_accepts_the_lb_form():
    import mcp_server
    out = mcp_server.coach_log_session(date="2026-10-01", exercises=[
        {"name": "Cable Fly", "sets": 2, "reps": [12, 12], "weight": [15, 15],
         "unit": "lb", "load_type": "per_side"}])
    assert "session_id" in out, out
    assert _stored("entered_unit") == "lb" and _stored("load_type") == "per_side"


# --- tonnage ---------------------------------------------------------------------------

def _tonnage(load_type: str | None, muscle=MuscleGroup.biceps) -> dict:
    log_session(SessionInput(date=T, exercises=[ExerciseModel(
        name="Hammer Curl", muscle_group=muscle, sets=2, reps=[10, 10],
        weight_kg=[10.0, 10.0], load_type=load_type)]))
    return get_specialization_trend(muscle, window_days=7).detail


@pytest.mark.parametrize("load_type", ["per_hand", "per_side"])
def test_both_limb_readings_double_the_tonnage(load_type):
    detail = _tonnage(load_type)
    assert detail["tonnage_kg"] == pytest.approx(2 * 10 * 10 * 2)
    assert detail["load_type_unknown_sets"] == 0


@pytest.mark.parametrize("load_type", ["total", "machine_stack", "bodyweight"])
def test_single_reading_load_types_count_once(load_type):
    assert _tonnage(load_type)["tonnage_kg"] == pytest.approx(2 * 10 * 10)


def test_unknown_load_type_is_counted_as_read_and_flagged():
    detail = _tonnage(None)
    assert detail["tonnage_kg"] == pytest.approx(2 * 10 * 10)  # not guessed
    assert detail["load_type_unknown_sets"] == 2


def test_hard_sets_do_not_depend_on_load_type():
    # volume currency is sets, never load — load_type only touches tonnage
    a = _tonnage("per_hand")
    report = get_specialization_trend(MuscleGroup.biceps, window_days=7)
    assert report.effective_volume == pytest.approx(2.0)
    assert a["unloaded_sets"] == 0


def test_load_type_vocabulary():
    assert {lt.value for lt in LoadType} == {
        "per_hand", "per_side", "total", "machine_stack", "bodyweight"}


def test_a_converted_model_revalidates_unchanged():
    """Nesting, read-back and amend re-validate models: conversion must be a
    no-op the second time (both forms present and agreeing)."""
    e = ExerciseModel(name="x", sets=2, reps=[10, 10], weight=[25, 30], unit="lb")
    again = ExerciseModel.model_validate(e.model_dump())
    assert again.weight_kg == e.weight_kg and again.weight == [25, 30]
    assert SessionInput(date=T, exercises=[e]).exercises[0].weight_kg == e.weight_kg


def test_null_weight_means_not_given():
    e = ExerciseModel(name="x", sets=1, reps=[5], weight_kg=[40], weight=None, unit=None)
    assert e.weight == [] and e.weight_kg == [40]


def test_docs_teach_that_single_arm_movements_are_total():
    """per_hand / per_side double tonnage; the Coach must know a one-arm
    movement is `total`, or its tonnage is silently doubled."""
    from pathlib import Path
    import inspect
    import mcp_server
    persona = (Path(mcp_server.__file__).parent / "docs" / "COACH_PROMPT.md").read_text(encoding="utf-8")
    for text in (persona, inspect.getdoc(mcp_server.coach_log_session)):
        assert "single-arm" in text and "total" in text
