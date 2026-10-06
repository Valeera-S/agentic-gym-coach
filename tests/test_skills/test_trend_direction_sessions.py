"""P60: the >=4 gate for direction / stalled counts DIRECTION sessions (the muscle
is a chart primary of some exercise), not every session crediting it."""

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


def test_overlap_only_sessions_do_not_open_the_direction_gate():
    # 2 chest days + 2 back days (Straight Arm Pulldown credits chest secondarily)
    _log(6, _ex("Cable Fly", 4.5, 12))
    _log(5, _ex("Straight Arm Pulldown", 20.0, 12))
    _log(2, _ex("Cable Fly", 5.7, 12))
    _log(1, _ex("Straight Arm Pulldown", 20.0, 12))
    r = _trend(M.chest)
    assert r.sessions_in_window == 4
    assert r.detail["direction_sessions"] == 2
    assert r.trend_direction is TrendDirection.unknown
    assert r.stalled is False
    assert r.detail["direction_basis"] is None


def test_four_direction_sessions_still_open_the_gate():
    for d, kg in ((6, 4.5), (4, 4.5), (2, 5.7), (0, 5.7)):
        _log(d, _ex("Cable Fly", kg, 12))
    r = _trend(M.chest)
    assert r.sessions_in_window == r.detail["direction_sessions"] == 4
    assert r.trend_direction is TrendDirection.up


def test_halves_split_over_direction_sessions_only():
    # direction sessions: 4.5, 4.5 | 5.7, 5.7 -> up. Overlap-only sessions
    # interleaved must not shift the split (6 sessions total, 3|3 would mix).
    _log(9, _ex("Cable Fly", 4.5, 12))
    _log(8, _ex("Straight Arm Pulldown", 20.0, 12))
    _log(7, _ex("Cable Fly", 4.5, 12))
    _log(6, _ex("Straight Arm Pulldown", 20.0, 12))
    _log(2, _ex("Cable Fly", 5.7, 12))
    _log(0, _ex("Cable Fly", 5.7, 12))
    r = _trend(M.chest, days=14)
    assert r.sessions_in_window == 6 and r.detail["direction_sessions"] == 4
    assert r.trend_direction is TrendDirection.up


def test_empty_window_reports_zero_direction_sessions():
    assert _trend(M.chest).detail["direction_sessions"] == 0
