"""Trend direction on performance — double progression, per exercise identity.

The old fallback (no <=6-rep sets in both window halves — most hypertrophy
training) compared hard-set COUNTS, so a user adding load at constant sets
was told they had stalled. Direction now follows performance per identity.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from models import ExerciseModel, MuscleGroup, SessionInput, TrendDirection
from skills.session_logger import log_session
from skills.trend_analysis import get_specialization_trend

END = date(2026, 10, 4)
M = MuscleGroup


def _log(days_ago: int, *exercises: ExerciseModel) -> None:
    log_session(SessionInput(date=END - timedelta(days=days_ago), exercises=list(exercises)))


def _ex(name: str, load: float | None, reps: float, sets: int = 4, **kw) -> ExerciseModel:
    return ExerciseModel(name=name, sets=sets, reps=[reps] * sets,
                         weight_kg=[load] * sets if load is not None else [], **kw)


def _trend(muscle: MuscleGroup, days: int = 28):
    return get_specialization_trend(muscle, window_days=days, end_date=END)


def test_the_users_real_lat_pulldown_progression_is_up_not_stalled():
    """ISSUES P19: Lat Pulldown 50, 50, 60, 60 lb at a constant 4x10, constant sets."""
    for days_ago, lb in ((13, 50), (9, 50), (5, 60), (0, 60)):
        _log(days_ago, ExerciseModel(name="Lat Pulldown", sets=4, reps=[10] * 4,
                                     weight=[lb] * 4, unit="lb"))
    r = _trend(M.lats)
    assert r.trend_direction is TrendDirection.up
    assert r.stalled is False
    assert r.detail["direction_basis"] == "performance"
    assert r.detail["identity_directions"] == {"Lat Pulldown": "up"}


def test_constant_load_and_reps_over_four_sessions_is_plateau_and_stalled():
    for days_ago in (13, 9, 5, 0):
        _log(days_ago, _ex("Dumbbell Lateral Raise", 6.8, 10))
    r = _trend(M.side_delt)
    assert r.trend_direction is TrendDirection.plateau and r.stalled is True


def test_more_reps_at_the_same_load_is_up():
    for days_ago, reps in ((13, 10), (9, 10), (5, 11), (0, 12)):
        _log(days_ago, _ex("Row", 20.0, reps))
    assert _trend(M.lats).trend_direction is TrendDirection.up


def test_less_load_at_the_same_reps_is_down():
    for days_ago, kg in ((13, 30), (9, 30), (5, 25), (0, 25)):
        _log(days_ago, _ex("Cable Fly", kg, 12))
    assert _trend(M.chest).trend_direction is TrendDirection.down


def test_a_deliberate_drop_on_one_identity_does_not_drag_another():
    """ISSUES P19: dumbbell fly progressing 15 -> 20 lb/hand while the cable fly
    was deliberately lowered for form — separate identities, separate verdicts;
    more up-sets than down-sets wins."""
    for days_ago, db, cable in ((13, 15, 15), (9, 15, 15), (5, 20, 10), (0, 20, 12.5)):
        _log(days_ago,
             ExerciseModel(name="Dumbbell Fly", sets=4, reps=[12] * 4, weight=[db] * 4, unit="lb"),
             ExerciseModel(name="Cable Fly", sets=3, reps=[12] * 3, weight=[cable] * 3, unit="lb"))
    r = _trend(M.chest)
    assert r.detail["identity_directions"] == {"Cable Fly": "down", "Dumbbell Fly": "up"}
    assert r.trend_direction is TrendDirection.up  # 16 up-sets vs 12 down-sets


def test_equal_up_and_down_sets_is_plateau():
    for days_ago, a, b in ((13, 10, 10), (9, 10, 10), (5, 12, 8), (0, 12, 8)):
        _log(days_ago, _ex("Dumbbell Fly", a, 12), _ex("Cable Fly", b, 12))
    assert _trend(M.chest).trend_direction is TrendDirection.plateau


def test_set_count_changes_alone_are_not_progression():
    for days_ago, sets in ((13, 2), (9, 2), (5, 6), (0, 6)):
        _log(days_ago, _ex("Lat Pulldown", 27.0, 10, sets=sets))
    r = _trend(M.lats)
    assert r.trend_direction is TrendDirection.plateau  # same load, same reps
    for days_ago, sets in ((13, 6), (9, 6), (5, 2), (0, 2)):
        _log(days_ago, _ex("Pull-Up", None, 8, sets=sets))
    assert _trend(M.lats).trend_direction is TrendDirection.plateau


def test_bodyweight_exercises_compare_reps():
    for days_ago, reps in ((13, 6), (9, 6), (5, 8), (0, 8)):
        _log(days_ago, _ex("Pull-Up", None, reps, sets=3))
    assert _trend(M.lats).trend_direction is TrendDirection.up


def test_only_primary_credits_decide_direction():
    """Rows credit rear delts (overlap); a regressing Row must not decide the
    rear-delt verdict — the rear-delt isolation does."""
    for days_ago, row_kg, rev_kg in ((13, 40, 5), (9, 40, 5), (5, 30, 6), (0, 30, 6)):
        _log(days_ago, _ex("Cable Row", row_kg, 10), _ex("Machine Reverse Fly", rev_kg, 15))
    r = _trend(M.rear_delt)
    assert r.detail["identity_directions"] == {"Machine Reverse Fly": "up"}
    assert r.trend_direction is TrendDirection.up
    assert r.effective_volume == pytest.approx(32.0)  # overlap still counts toward volume


def test_every_chart_primary_decides_rows_decide_mid_back_and_rdl_decides_hamstrings():
    """Human verdict on F5: all chart-listed primaries decide direction."""
    for days_ago, kg in ((13, 40), (9, 40), (5, 45), (0, 45)):
        _log(days_ago, _ex("Cable Row", kg, 10), _ex("Romanian Deadlift", kg * 2, 8))
    assert _trend(M.mid_back).trend_direction is TrendDirection.up
    assert _trend(M.hamstrings).trend_direction is TrendDirection.up
    assert _trend(M.erectors).trend_direction is TrendDirection.up


def test_conditional_primary_triceps_on_dips():
    for days_ago, reps in ((13, 8), (9, 8), (5, 10), (0, 10)):
        _log(days_ago, _ex("Dip", None, reps, sets=3))
    assert _trend(M.triceps).trend_direction is TrendDirection.up


def test_identities_done_in_only_one_half_are_not_compared():
    for days_ago in (13, 9):
        _log(days_ago, _ex("Cable Fly", 10, 12))
    for days_ago, kg in ((5, 10), (0, 12)):
        _log(days_ago, _ex("Dumbbell Fly", kg, 12))
    r = _trend(M.chest)
    assert r.trend_direction is TrendDirection.unknown and r.stalled is False


# --- the load step of double progression (human decision 2026-10-05) ---------

def test_textbook_double_progression_load_step_is_up():
    """20 kg x 13-15, then 22.5 kg x 12-13: the load went up and the heavier set
    is still a working set whose Epley estimate did not drop."""
    for days_ago, kg, reps in ((13, 20, 13), (9, 20, 15), (5, 22.5, 12), (0, 22.5, 13)):
        _log(days_ago, _ex("Dumbbell Curl", kg, reps, sets=3))
    r = _trend(M.biceps)
    assert r.detail["identity_directions"] == {"Dumbbell Curl": "up"}
    assert r.trend_direction is TrendDirection.up and r.stalled is False


def test_a_load_jump_that_collapses_reps_is_not_progress():
    """20 x 15 -> 22.5 x 6: heavier, but the Epley estimate drops (30 -> 27)."""
    for days_ago, kg, reps in ((13, 20, 15), (9, 20, 15), (5, 22.5, 6), (0, 22.5, 6)):
        _log(days_ago, _ex("Dumbbell Curl", kg, reps, sets=3))
    assert _trend(M.biceps).detail["identity_directions"] == {"Dumbbell Curl": "flat"}


def test_a_heavier_set_below_working_reps_is_not_progress():
    for days_ago, kg, reps in ((13, 20, 8), (9, 20, 8), (5, 30, 5), (0, 30, 5)):
        _log(days_ago, _ex("Lat Pulldown", kg, reps))  # 5 reps: not a working set here
    r = _trend(M.lats)
    assert r.detail["direction_basis"] == "performance"
    assert r.detail["identity_directions"] == {"Lat Pulldown": "flat"}


def test_the_load_step_is_symmetric_for_down():
    """22.5 x 12 -> 20 x 13: lighter, and the earlier heavier set held a higher
    Epley estimate — a step back."""
    for days_ago, kg, reps in ((13, 22.5, 12), (9, 22.5, 12), (5, 20, 13), (0, 20, 13)):
        _log(days_ago, _ex("Dumbbell Curl", kg, reps, sets=3))
    assert _trend(M.biceps).trend_direction is TrendDirection.down


def test_adding_external_load_to_a_bodyweight_exercise_is_up():
    for days_ago, kg in ((13, None), (9, None), (5, 5.0), (0, 5.0)):
        _log(days_ago, _ex("Pull-Up", kg, 8, sets=3))
    assert _trend(M.lats).trend_direction is TrendDirection.up


# --- an unrecorded weight is unknown, never zero ---------------------------------

@pytest.mark.parametrize("second_half", [[None] * 4, []])
def test_unrecorded_weights_on_a_loaded_exercise_are_not_a_regression(second_half):
    """Lat Pulldown 27 kg, then the same sets with the weight not logged
    (nulls, or no weight array at all): that is missing data, not 0 kg."""
    for days_ago in (13, 9):
        _log(days_ago, _ex("Lat Pulldown", 27.0, 10))
    for days_ago in (5, 0):
        _log(days_ago, ExerciseModel(name="Lat Pulldown", sets=4, reps=[10] * 4,
                                     weight_kg=second_half))
    r = _trend(M.lats)
    assert r.trend_direction is TrendDirection.unknown
    assert r.stalled is False


def test_unrecorded_first_half_is_not_a_gain():
    for days_ago in (13, 9):
        _log(days_ago, ExerciseModel(name="Lat Pulldown", sets=4, reps=[10] * 4))
    for days_ago in (5, 0):
        _log(days_ago, _ex("Lat Pulldown", 27.0, 10))
    assert _trend(M.lats).trend_direction is TrendDirection.unknown


def test_a_partly_recorded_half_compares_only_its_recorded_sets():
    for days_ago in (13, 9):
        _log(days_ago, _ex("Lat Pulldown", 27.0, 10))
    for days_ago in (5, 0):
        _log(days_ago, ExerciseModel(name="Lat Pulldown", sets=4, reps=[10] * 4,
                                     weight_kg=[30.0, None, None, None]))
    assert _trend(M.lats).trend_direction is TrendDirection.up


def test_legacy_bodyweight_rows_without_load_type_still_compare_reps():
    from skills.init import get_duckdb
    for days_ago, reps in ((13, 6), (9, 6), (5, 8), (0, 8)):
        get_duckdb().execute(
            "INSERT INTO sessions (date, phase, exercises) VALUES (?, 'maintenance', ?)",
            [END - timedelta(days=days_ago),
             [{"name": "Pull-Up", "muscle_group": "lats", "sets": 3, "reps": [reps] * 3,
               "rpe": [None] * 3, "weight_kg": [None] * 3}]])  # as the logger padded them
    assert _trend(M.lats).trend_direction is TrendDirection.up


def test_unknown_names_group_case_insensitively():
    for days_ago, name, kg in ((13, "Meadows Row", 20), (9, "meadows row", 20),
                               (5, "MEADOWS ROW", 22.5), (0, "Meadows Row", 22.5)):
        _log(days_ago, _ex(name, kg, 10))
    r = _trend(M.lats)
    assert r.trend_direction is TrendDirection.up
    assert len(r.detail["identity_directions"]) == 1


def test_a_caller_override_on_a_catalog_exercise_adds_volume_but_never_decides():
    """Human decision: the chart's primaries decide direction."""
    for days_ago, kg in ((13, 60), (9, 60), (5, 70), (0, 70)):
        _log(days_ago, _ex("Barbell Bench Press", kg, 8, muscle_group="triceps"))
    r = _trend(M.triceps)
    assert r.effective_volume == pytest.approx(16.0)
    assert r.trend_direction is TrendDirection.unknown


def test_empty_window_detail_has_a_stable_shape():
    r = _trend(M.calves)
    assert r.detail["direction_basis"] is None and r.detail["identity_directions"] == {}


@pytest.mark.slow
def test_performance_path_is_fast_on_10k_rows():
    import time
    from skills.init import get_duckdb
    get_duckdb().execute(
        "INSERT INTO sessions (date, phase, exercises) "
        "SELECT DATE '2026-10-04' - CAST(i % 28 AS INTEGER), 'maintenance', "
        "[{'name': 'Lat Pulldown', 'muscle_group': 'lats', 'sets': 4, "
        "'reps': [10.0, 10.0, 9.0, 8.0], 'rpe': [8.0, 8.0, 9.0, 9.0], "
        "'weight_kg': [40.0, 40.0, 40.0, 40.0], "
        "'load_type': 'machine_stack'}] FROM range(10000) t(i)")
    _trend(M.lats)  # warm up
    t0 = time.perf_counter()
    r = _trend(M.lats)
    dt_ms = (time.perf_counter() - t0) * 1000
    assert r.detail["direction_basis"] == "performance"
    assert dt_ms < 100, f"trend took {dt_ms:.1f}ms"


def test_est_1rm_path_is_unchanged_when_heavy_sets_exist_in_both_halves():
    for days_ago, kg in ((13, 100), (9, 100), (5, 110), (0, 110)):
        _log(days_ago, _ex("Barbell Bench Press", kg, 5, sets=3))
    r = _trend(M.chest)
    assert r.detail["direction_basis"] == "est_1rm"
    assert r.trend_direction is TrendDirection.up
    assert r.detail["identity_directions"] == {}


def test_fewer_than_four_sessions_is_unknown():
    for days_ago in (5, 0):
        _log(days_ago, _ex("Lat Pulldown", 27, 10))
    r = _trend(M.lats)
    assert r.trend_direction is TrendDirection.unknown and r.detail["direction_basis"] is None


def test_unknown_names_decide_through_their_stored_primary():
    for days_ago, kg in ((13, 20), (9, 20), (5, 22.5), (0, 22.5)):
        _log(days_ago, _ex("Meadows Row", kg, 10))  # keyword guess: lats
    assert _trend(M.lats).trend_direction is TrendDirection.up



# --- load-step details (human decisions on F10 r2) --------------------------------

def test_equal_epley_is_not_lost_to_float_rounding():
    """20 lb x 15 -> 25 lb x 6: both Epley 30 — "does not drop" includes equal."""
    for days_ago, lb, reps in ((13, 20, 15), (9, 20, 15), (5, 25, 6), (0, 25, 6)):
        _log(days_ago, ExerciseModel(name="Dumbbell Curl", sets=3, reps=[reps] * 3,
                                     weight=[lb] * 3, unit="lb"))
    assert _trend(M.biceps).detail["identity_directions"] == {"Dumbbell Curl": "up"}


def test_equal_epley_mirror_is_down():
    for days_ago, lb, reps in ((13, 25, 6), (9, 25, 6), (5, 20, 15), (0, 20, 15)):
        _log(days_ago, ExerciseModel(name="Dumbbell Curl", sets=3, reps=[reps] * 3,
                                     weight=[lb] * 3, unit="lb"))
    assert _trend(M.biceps).detail["identity_directions"] == {"Dumbbell Curl": "down"}


def test_every_set_at_the_heavier_load_must_reach_six_reps():
    for days_ago in (13, 9):
        _log(days_ago, _ex("Dumbbell Curl", 20, 12, sets=2))
    for days_ago in (5, 0):
        _log(days_ago, ExerciseModel(name="Dumbbell Curl", sets=2, reps=[8, 4],
                                     weight_kg=[22.5, 22.5]))
    assert _trend(M.biceps).detail["identity_directions"] == {"Dumbbell Curl": "flat"}


@pytest.mark.parametrize("first, second, expected", [
    ((None, 15), (2.5, 6), "flat"),   # rep collapse: no load step for bodyweight
    ((5.0, 15), (10.0, 6), "flat"),
    ((None, 8), (5.0, 8), "up"),      # more added load at the same reps: dominance
    ((5.0, 8), (5.0, 10), "up"),
])
def test_bodyweight_exercises_compare_by_dominance_only(first, second, expected):
    for days_ago, (kg, reps) in ((13, first), (9, first), (5, second), (0, second)):
        _log(days_ago, _ex("Pull-Up", kg, reps, sets=3))
    assert _trend(M.lats).detail["identity_directions"] == {"Pull-Up": expected}



@pytest.mark.parametrize("name", ["Pull-Up", "Dip"])
@pytest.mark.parametrize("declared", ["total", "per_hand"])
@pytest.mark.parametrize("first, second", [((5.0, 15), (10.0, 6)), ((10.0, 6), (5.0, 15))])
def test_a_declared_load_type_never_turns_the_load_step_on_for_bodyweight_exercises(
        name, declared, first, second):
    """A belt-weighted pull-up logged as `total` is still a bodyweight exercise:
    its true load includes a body mass the system does not have."""
    for days_ago, (kg, reps) in ((13, first), (9, first), (5, second), (0, second)):
        _log(days_ago, _ex(name, kg, reps, sets=3, load_type=declared))
    muscle = M.lats if name == "Pull-Up" else M.triceps
    assert _trend(muscle).detail["identity_directions"] == {name: "flat"}


def test_a_declared_total_with_null_weights_is_still_unrecorded():
    for days_ago in (13, 9, 5, 0):
        _log(days_ago, ExerciseModel(name="Pull-Up", sets=3, reps=[8] * 3, load_type="total"))
    assert _trend(M.lats).trend_direction is TrendDirection.unknown


# --- P49: sessions are counted by id; halves split by session order -----------

def test_two_training_sessions_on_one_day_count_as_two():
    for days_ago in (1, 0):
        _log(days_ago, _ex("Lat Pulldown", 50.0, 10))
        _log(days_ago, _ex("Lat Pulldown", 50.0, 10))
    r = _trend(M.lats)
    assert r.sessions_in_window == 4
    assert r.trend_direction is TrendDirection.plateau and r.stalled is True


def test_halves_are_the_first_floor_n_over_2_sessions_not_halves_of_dates():
    # sessions in order: 20, 30 | 30, 20 -> equal tops -> plateau. Splitting the
    # distinct DATES instead (D-1 | D) would read 20 -> 30 as `up`.
    _log(1, _ex("Lat Pulldown", 20.0, 10))
    for kg in (30.0, 30.0, 20.0):
        _log(0, _ex("Lat Pulldown", kg, 10))
    r = _trend(M.lats)
    assert r.sessions_in_window == 4
    assert r.trend_direction is TrendDirection.plateau


def test_habit_sessions_are_not_counted_as_sessions():
    for days_ago in (3, 2, 1, 0):
        _log(days_ago, _ex("Lat Pulldown", 50.0, 10))
    log_session(SessionInput(date=END, kind="habit", exercises=[_ex("Lat Pulldown", 50.0, 10)]))
    assert _trend(M.lats).sessions_in_window == 4
