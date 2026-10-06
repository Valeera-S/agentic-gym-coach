"""trend_analysis edge cases — null RPE, session-count gate, window extremes."""

from datetime import date, timedelta

import pytest

from models import ExerciseModel, MuscleGroup, SessionInput
from skills.session_logger import log_session
from skills.trend_analysis import get_specialization_trend, hard_sets_by_muscle

T = date(2030, 1, 10)


def _log(d: date, weights: list[float | None], reps: float = 5.0,
         rpes: list[float | None] | None = None, name: str = "Cable Lateral Raise"):
    n = len(weights)
    exs = [ExerciseModel(
        name=name, sets=n, reps=[reps] * n,
        rpe=(rpes if rpes is not None else [9.0] * n), weight_kg=weights,
    )]
    log_session(SessionInput(date=d, exercises=exs))


def test_hard_sets_empty_window_is_empty_dict():
    assert hard_sets_by_muscle(date(2030, 3, 1), date(2030, 3, 28)) == {}


def test_null_rpe_gives_null_avg_without_crashing():
    _log(T - timedelta(days=1), [10.0, 10.0], rpes=[None, 8.0])
    r = get_specialization_trend(MuscleGroup.side_delt, window_days=28, end_date=T)
    assert r.avg_rpe == pytest.approx(8.0)  # nulls excluded from mean
    assert r.effective_volume == pytest.approx(2.0)


def test_all_null_rpe_gives_null_avg():
    _log(T - timedelta(days=1), [10.0], rpes=[None])
    r = get_specialization_trend(MuscleGroup.side_delt, window_days=28, end_date=T)
    assert r.avg_rpe is None


def test_null_weight_excluded_from_est_1rm_but_counts_as_set():
    _log(T - timedelta(days=1), [None, 60.0], reps=5)
    r = get_specialization_trend(MuscleGroup.side_delt, window_days=28, end_date=T)
    assert r.effective_volume == pytest.approx(2.0)  # bodyweight set counts
    assert r.detail["unloaded_sets"] == 1
    # only the 60kg set qualifies: 60 * (1 + 5/30) = 70.0
    assert r.est_1rm_kg == pytest.approx(70.0, abs=0.1)


def test_trend_needs_four_sessions_for_direction():
    for i in range(3):
        _log(T - timedelta(days=3 - i), [50.0 - 10 * i], reps=5)
    r = get_specialization_trend(MuscleGroup.side_delt, window_days=28, end_date=T)
    assert r.sessions_in_window == 3
    assert r.trend_direction == "unknown"
    assert r.stalled is False


def test_stalled_requires_plateau_or_down():
    for i in range(4):
        _log(T - timedelta(days=4 - i), [40.0], reps=5)
    r = get_specialization_trend(MuscleGroup.side_delt, window_days=28, end_date=T)
    assert r.trend_direction == "plateau"
    assert r.stalled is True


def test_negative_window_returns_empty_report():
    # CHARACTERIZATION (adversarial F7): inverted window silently means "no data".
    r = get_specialization_trend(MuscleGroup.side_delt, window_days=-28, end_date=T)
    assert r.effective_volume == 0.0
    assert r.sessions_in_window == 0


def test_low_form_tonnage_is_total_external_load_not_discounted():
    # P48: the 50% form discount is for effective hard sets only
    log_session(SessionInput(date=T - timedelta(days=1), exercises=[ExerciseModel(
        name="Dumbbell Curl", sets=2, reps=[10, 10], weight_kg=[20.0, 20.0],
        form_quality=2, load_type="per_hand")]))
    r = get_specialization_trend(MuscleGroup.biceps, window_days=28, end_date=T)
    assert r.detail["tonnage_kg"] == pytest.approx(800.0)   # 2 x 10 x 20 x 2 hands
    assert r.effective_volume == pytest.approx(1.0)          # sets still discounted


def test_form_discount_applies_to_sets():
    _log(T - timedelta(days=1), [100.0, 100.0, 100.0], reps=5)
    # make one set low-quality by rewriting form via a second low-form exercise
    exs = [ExerciseModel(name="Squat", sets=2, reps=[5, 5], rpe=[8, 8],
                         weight_kg=[100.0, 100.0], form_quality=2)]
    log_session(SessionInput(date=T - timedelta(days=2), exercises=exs))
    vol = hard_sets_by_muscle(T - timedelta(days=7), T)
    assert vol["quads"] == pytest.approx(1.0)   # 2 sets x 0.5
    assert vol["side_delt"] == pytest.approx(3.0)


# --- P26: a NULL per-set array reads as "every set unrecorded" ----------------

_NULL_ARRAY_UPDATE = """
    UPDATE sessions SET exercises = list_transform(exercises, x -> struct_pack(
        name := x.name, muscle_group := x.muscle_group, sets := x.sets,
        reps := {reps}, rpe := {rpe}, weight_kg := {weight},
        tempo := x.tempo, form_quality := x.form_quality, pain_flag := x.pain_flag,
        notes := x.notes, raw_name := x.raw_name, muscle_source := x.muscle_source,
        load_type := x.load_type, entered_weight := x.entered_weight,
        entered_unit := x.entered_unit))
"""


@pytest.mark.parametrize("nulled", [("weight",), ("rpe",), ("reps",),
                                    ("reps", "rpe"), ("reps", "rpe", "weight")])
def test_null_set_arrays_do_not_crash_readers_and_count_as_unrecorded(nulled):
    from coach_tools import DISPATCH
    from skills.init import get_duckdb
    from skills.snapshot import generate_phase_snapshot
    today = date.today()
    for back in (3, 2, 1, 0):
        _log(today - timedelta(days=back), [40.0, 40.0, 40.0], name="Squat")
    cols = {"reps": "x.reps", "rpe": "x.rpe", "weight": "x.weight_kg"}
    for k in nulled:
        cols[k] = "NULL::DOUBLE[]" if k == "weight" else "NULL::FLOAT[]"
    get_duckdb().execute(_NULL_ARRAY_UPDATE.format(**cols))

    r = get_specialization_trend(MuscleGroup.quads, window_days=28, end_date=today)
    assert r.effective_volume == pytest.approx(12.0)      # sets still count
    assert r.sessions_in_window == 4
    if "weight" in nulled:                                # padded to `sets`, not 1 row
        assert r.detail["unloaded_sets"] == 12
        assert r.est_1rm_kg is None
    assert "tonnage_kg" in r.detail
    generate_phase_snapshot()                             # explodes the same arrays
    DISPATCH["recovery"]({})
    DISPATCH["sessions"]({})
    DISPATCH["session_detail"]({"date": str(today)})


# --- P28: explode() states Polars' current empty-array behavior ---------------

def test_empty_arrays_pad_to_sets_without_polars_warnings():
    import warnings
    from skills.init import get_duckdb
    from skills.snapshot import generate_phase_snapshot
    today = date.today()
    _log(today - timedelta(days=1), [40.0, 40.0, 40.0], name="Squat")
    get_duckdb().execute(_NULL_ARRAY_UPDATE.format(
        reps="[]::FLOAT[]", rpe="[]::FLOAT[]", weight="[]::DOUBLE[]"))
    with warnings.catch_warnings():
        # Polars 2.0 flips explode's empty_as_null default; the call sites pin
        # today's value, so neither a warning nor a changed count may appear
        warnings.simplefilter("error")
        r = get_specialization_trend(MuscleGroup.quads, window_days=28, end_date=today)
        generate_phase_snapshot()
    assert r.effective_volume == pytest.approx(3.0)   # hard sets come from `sets`, not rows
    assert r.detail["unloaded_sets"] == 3             # P47: an empty array pads to `sets` null rows


# --- P29: window_days=N is exactly N calendar days ending at end_date ---------

def test_window_includes_day_n_minus_1_before_end_and_excludes_day_n():
    _log(T - timedelta(days=6), [40.0])   # N-1 days before end: inside a 7-day window
    _log(T - timedelta(days=7), [40.0])   # N days before end: outside
    r = get_specialization_trend(MuscleGroup.side_delt, window_days=7, end_date=T)
    assert r.effective_volume == pytest.approx(1.0)
    assert r.sessions_in_window == 1


def test_window_includes_end_date_itself():
    _log(T, [40.0])
    r = get_specialization_trend(MuscleGroup.side_delt, window_days=1, end_date=T)
    assert r.effective_volume == pytest.approx(1.0)
    _log(T - timedelta(days=1), [40.0])
    r = get_specialization_trend(MuscleGroup.side_delt, window_days=1, end_date=T)
    assert r.effective_volume == pytest.approx(1.0)


def test_snapshot_window_is_28_days_not_29():
    from skills.snapshot import generate_phase_snapshot
    today = date.today()
    for back in (27, 28):
        log_session(SessionInput(date=today - timedelta(days=back), exercises=[
            ExerciseModel(name="Incline Bench Press" if back == 27 else "Pull-Up",
                          sets=1, reps=[5], rpe=[9], weight_kg=[40.0], form_quality=5)]))
    lifts = generate_phase_snapshot().specialization_lifts
    assert "Incline Bench Press" in lifts      # day 27 back: last day of the 28-day window
    assert "Pull-Up" not in lifts              # day 28 back: outside


# --- P47: legacy rows — nameless entries and mismatched per-set arrays ---------

def test_nameless_entry_credits_no_muscle_and_does_not_crash_readers():
    from skills.init import get_duckdb
    from skills.snapshot import generate_phase_snapshot
    today = date.today()
    _log(today - timedelta(days=1), [40.0, 40.0], name="Squat")
    get_duckdb().execute(_NULL_ARRAY_UPDATE.format(
        reps="x.reps", rpe="x.rpe", weight="x.weight_kg").replace(
        "name := x.name", "name := NULL::VARCHAR"))
    vol = hard_sets_by_muscle(today - timedelta(days=7), today)
    assert vol == {}                                   # credits no muscle
    generate_phase_snapshot()
    r = get_specialization_trend(MuscleGroup.quads, window_days=28, end_date=today)
    assert r.effective_volume == 0.0


@pytest.mark.parametrize("reps,rpe,weight,n_weights", [
    ("[5, 5]::FLOAT[]", "[8]::FLOAT[]", "[100, 100, 100, 100]::DOUBLE[]", 3),  # short + long
    ("[5]::FLOAT[]", "[]::FLOAT[]", "[100]::DOUBLE[]", 1),
    ("[5, 5, 5, 5, 5]::FLOAT[]", "[8, 8, 8, 8, 8]::FLOAT[]", "[100, 100, 100, 100, 100]::DOUBLE[]", 3),
])
def test_mismatched_set_array_lengths_align_to_sets(reps, rpe, weight, n_weights):
    from skills.init import get_duckdb
    from skills.snapshot import generate_phase_snapshot
    today = date.today()
    for back in (3, 2, 1, 0):
        _log(today - timedelta(days=back), [40.0, 40.0, 40.0], name="Squat")
    get_duckdb().execute(_NULL_ARRAY_UPDATE.format(reps=reps, rpe=rpe, weight=weight))
    r = get_specialization_trend(MuscleGroup.quads, window_days=28, end_date=today)
    assert r.effective_volume == pytest.approx(12.0)    # sets, not array lengths
    generate_phase_snapshot()
    # unrecorded (padded) weights are counted per stored `sets`, 3 per session
    assert r.detail["unloaded_sets"] == 4 * (3 - n_weights)


# --- P55: a recorded 0-rep set is neither a hard set nor an est-1RM source -----

def test_zero_rep_set_is_not_a_hard_set_and_gives_no_1rm():
    from skills.init import get_duckdb
    today = date.today()
    _log(today - timedelta(days=1), [200.0, 100.0, 100.0], name="Squat")
    get_duckdb().execute(_NULL_ARRAY_UPDATE.format(
        reps="[0, 5, 5]::FLOAT[]", rpe="x.rpe", weight="x.weight_kg"))
    r = get_specialization_trend(MuscleGroup.quads, window_days=28, end_date=today)
    assert r.est_1rm_kg == pytest.approx(100 * (1 + 5 / 30), abs=0.1)   # not 200
    assert r.effective_volume == pytest.approx(2.0)
    assert hard_sets_by_muscle(today - timedelta(days=7), today)["quads"] == pytest.approx(2.0)


def test_unrecorded_rep_count_still_counts_as_a_hard_set():
    from skills.init import get_duckdb
    today = date.today()
    _log(today - timedelta(days=1), [100.0, 100.0], name="Squat")
    get_duckdb().execute(_NULL_ARRAY_UPDATE.format(
        reps="NULL::FLOAT[]", rpe="x.rpe", weight="x.weight_kg"))
    assert hard_sets_by_muscle(today - timedelta(days=7), today)["quads"] == pytest.approx(2.0)
