"""Habit sessions — sets that count toward volume but are not training.

The user does 60 bodyweight squats every day. Logged as kind='habit' they add
to quad volume, but recovery must not think the user trained that day and the
session-gap / staleness signal must not be reset by them.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from coach_tools import DISPATCH, error_payload
from models import ExerciseModel, MuscleGroup, SessionInput, SessionKind
from skills.init import get_duckdb
from skills.recovery import compute_recovery_score
from skills.session_logger import log_session
from skills.snapshot import session_gap
from skills.trend_analysis import get_specialization_trend, hard_sets_by_muscle

D = date(2026, 10, 4)


def _squats(d: date, kind: str = "habit", sets: int = 3) -> None:
    log_session(SessionInput(date=d, kind=kind, exercises=[ExerciseModel(
        name="Bodyweight Squat", muscle_group="quads", sets=sets,
        reps=[20] * sets, load_type="bodyweight")]))


def _training(d: date) -> None:
    log_session(SessionInput(date=d, exercises=[ExerciseModel(
        name="Lat Pulldown", sets=4, reps=[10] * 4, weight_kg=[27.0] * 4)]))


def test_default_kind_is_training():
    _training(D)
    assert get_duckdb().execute("SELECT kind FROM sessions").fetchone()[0] == "training"
    assert SessionInput(date=D, exercises=[]).kind is SessionKind.training


def test_habit_sets_count_toward_volume():
    _squats(D - timedelta(days=1))
    assert hard_sets_by_muscle(D - timedelta(days=7), D)["quads"] == pytest.approx(3.0)
    report = get_specialization_trend(MuscleGroup.quads, window_days=7, end_date=D)
    assert report.effective_volume == pytest.approx(3.0)


def test_daily_habits_do_not_look_like_training_to_recovery():
    for days_ago in range(1, 8):  # a habit every day of the past week
        _squats(D - timedelta(days=days_ago))
    r = compute_recovery_score(D)
    assert r.components["trained_yesterday"] is False
    assert r.components["trained_day_before"] is False
    assert r.components["sets_7d"] == 0
    assert r.components["days_since_last_session"] is None
    assert r.score == compute_recovery_score(D + timedelta(days=365)).score  # as if empty


def test_habit_pain_is_not_a_recovery_input():
    log_session(SessionInput(date=D - timedelta(days=1), kind="habit", exercises=[
        ExerciseModel(name="Bodyweight Squat", sets=1, reps=[20], pain_flag=True)]))
    assert compute_recovery_score(D).components["pain_events_7d"] == 0


def test_training_still_counts_beside_habits():
    _training(D - timedelta(days=1))
    _squats(D - timedelta(days=1))
    _squats(D - timedelta(days=2))
    r = compute_recovery_score(D)
    assert r.components["trained_yesterday"] is True
    assert r.components["trained_day_before"] is False
    assert r.components["sets_7d"] == 4
    assert r.components["days_since_last_session"] == 1


def test_habits_do_not_reset_the_session_gap():
    _training(D - timedelta(weeks=10))
    for days_ago in range(0, 14):
        _squats(D - timedelta(days=days_ago))
    gap = session_gap(D)
    assert gap.weeks_since_last_session == pytest.approx(10.0)
    assert gap.reassessment_recommended is True


def test_habits_alone_leave_no_session_gap():
    _squats(D)
    assert session_gap(D).weeks_since_last_session is None


def test_habits_do_not_count_as_deload_sessions():
    from skills.snapshot import _block_state
    log_session(SessionInput(date=D - timedelta(days=3), kind="habit", phase="deload",
                             exercises=[ExerciseModel(name="Bodyweight Squat", sets=1, reps=[20])]))
    assert _block_state(D)["weeks_since_deload"] is None


def test_habits_do_not_outvote_the_programs_phase():
    from skills.phase import current_phase
    log_session(SessionInput(date=D - timedelta(days=9), phase="accumulation", exercises=[
        ExerciseModel(name="Lat Pulldown", sets=1, reps=[10])]))
    for days_ago in range(0, 5):
        log_session(SessionInput(date=D - timedelta(days=days_ago), kind="habit",
                                 phase="maintenance",
                                 exercises=[ExerciseModel(name="Bodyweight Squat", sets=1, reps=[20])]))
    assert current_phase().value == "accumulation"


@pytest.mark.parametrize("bad", ["Habit", "rest", "", None, 1])
def test_invalid_kind_is_invalid_input(bad):
    with pytest.raises(ValidationError) as exc:
        DISPATCH["log_session"]({"date": "2026-10-04", "kind": bad,
                                 "exercises": [{"name": "Squat", "sets": 1}]})
    assert error_payload(exc.value)["error"] == "invalid_input"
    assert get_duckdb().execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_kind_through_both_surfaces_and_listed_by_sessions():
    import mcp_server
    out = mcp_server.coach_log_session(date="2026-10-04", kind="habit",
                                       exercises=[{"name": "Bodyweight Squat", "sets": 3}])
    assert "session_id" in out, out
    assert mcp_server.coach_log_session(date="2026-10-03",
                                        exercises=[{"name": "Lat Pulldown", "sets": 3}])
    listed = DISPATCH["sessions"]({})
    assert [s["kind"] for s in listed] == ["habit", "training"]
