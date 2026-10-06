"""P59: an unknown trend direction says why (detail.direction_reason)."""

from __future__ import annotations

from datetime import date, timedelta

from models import ExerciseModel, MuscleGroup, SessionInput, TrendDirection
from skills.session_logger import log_session
from skills.trend_analysis import get_specialization_trend

END = date(2026, 10, 4)
M = MuscleGroup


def _log(days_ago: int, *exercises: ExerciseModel, kind: str = "training") -> None:
    log_session(SessionInput(date=END - timedelta(days=days_ago), kind=kind,
                             exercises=list(exercises)))


def _ex(name: str, load: float, reps: float, sets: int = 3) -> ExerciseModel:
    return ExerciseModel(name=name, sets=sets, reps=[reps] * sets, weight_kg=[load] * sets)


def _trend(muscle, days: int = 7):
    return get_specialization_trend(muscle, window_days=days, end_date=END)


def _reason(r):
    return r.detail["direction_reason"]


def test_no_sessions_reason():
    r = _trend(M.chest)
    assert r.trend_direction is TrendDirection.unknown
    assert _reason(r)["code"] == "no_sessions"
    assert _reason(r)["text"]


def test_habit_only_muscle_reason_is_not_not_trained():
    for d in range(0, 4):
        _log(d, ExerciseModel(name="Bodyweight Squat", sets=3, reps=[20] * 3), kind="habit")
    r = _trend(M.quads)
    assert r.effective_volume == 12.0 and r.sessions_in_window == 0
    assert _reason(r)["code"] == "only_habit_sessions"
    assert "habit" in _reason(r)["text"]


def test_too_few_direction_sessions_states_count_and_threshold():
    for d in (5, 3, 1):
        _log(d, _ex("Cable Fly", 4.5, 12))
    r = _trend(M.chest)
    assert _reason(r)["code"] == "too_few_direction_sessions"
    assert "3" in _reason(r)["text"] and "4" in _reason(r)["text"]


def test_overlap_only_sessions_read_as_too_few_direction_sessions():
    for d in (5, 3, 1, 0):
        _log(d, _ex("Straight Arm Pulldown", 20.0, 12))
    r = _trend(M.chest)
    assert r.sessions_in_window == 4 and r.detail["direction_sessions"] == 0
    assert _reason(r)["code"] == "too_few_direction_sessions"


def test_no_comparable_identity_reason():
    # four direction sessions, but each half trains a different exercise
    for d in (6, 5):
        _log(d, _ex("Cable Fly", 4.5, 12))
    for d in (1, 0):
        _log(d, _ex("Dumbbell Fly", 6.8, 12))
    r = _trend(M.chest)
    assert r.trend_direction is TrendDirection.unknown
    assert _reason(r)["code"] == "no_comparable_identity"


def test_known_direction_has_no_reason():
    for d, kg in ((6, 4.5), (4, 4.5), (2, 5.7), (0, 5.7)):
        _log(d, _ex("Cable Fly", kg, 12))
    assert _reason(_trend(M.chest)) is None

