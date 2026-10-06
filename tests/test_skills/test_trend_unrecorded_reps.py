"""P62: sets with no recorded rep count still count as hard sets (the book counts
sets), and detail.unrecorded_reps_sets shows how many such effective sets a total holds."""

from __future__ import annotations

from datetime import date, timedelta

from models import ExerciseModel, MuscleGroup, SessionInput
from skills.session_logger import log_session
from skills.trend_analysis import get_specialization_trend

END = date(2026, 10, 4)
M = MuscleGroup


def _log(days_ago: int, *exercises: ExerciseModel) -> None:
    log_session(SessionInput(date=END - timedelta(days=days_ago), exercises=list(exercises)))


def _trend(muscle):
    return get_specialization_trend(muscle, window_days=7, end_date=END)


def test_unrecorded_reps_sets_are_counted_and_shown():
    _log(1, ExerciseModel(name="Cable Fly", sets=5, reps=[12, 12, None, None, None],
                          weight_kg=[4.5] * 5))
    r = _trend(M.chest)
    assert r.effective_volume == 5.0  # the book counts sets
    assert r.detail["unrecorded_reps_sets"] == 3.0


def test_unrecorded_reps_sets_are_effective_sets():
    # no reps array at all: every set unrecorded, halved by form_quality < 3
    _log(1, ExerciseModel(name="Cable Fly", sets=4, form_quality=2))
    _log(0, ExerciseModel(name="Cable Fly", sets=3, reps=[10, None, None], weight_kg=[4.5] * 3))
    r = _trend(M.chest)
    assert r.detail["unrecorded_reps_sets"] == 4.0  # 4 * 0.5 + 2
    assert r.effective_volume == 5.0  # 4 * 0.5 + 3


def test_unrecorded_reps_sets_zero_when_all_recorded_and_when_empty():
    _log(1, ExerciseModel(name="Cable Fly", sets=3, reps=[12] * 3, weight_kg=[4.5] * 3))
    assert _trend(M.chest).detail["unrecorded_reps_sets"] == 0.0
    assert _trend(M.quads).detail["unrecorded_reps_sets"] == 0.0


def test_unrecorded_reps_sets_count_overlap_credit_too():
    # the total they qualify includes overlap sets, so they are counted alike
    _log(1, ExerciseModel(name="Straight Arm Pulldown", sets=2))
    r = _trend(M.chest)
    assert r.effective_volume == 2.0 and r.detail["unrecorded_reps_sets"] == 2.0
