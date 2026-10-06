"""Model-level validation edge cases (Pydantic boundary; no DB touched)."""

from datetime import date

import pytest

from models import AvailabilityWindow, ExerciseModel, SessionInput, UserProfile, Weekday

D = date(2030, 1, 10)


def _ex(**kw) -> dict:
    base = dict(name="Bench Press", sets=1, reps=[5], rpe=[8], weight_kg=[60.0])
    base.update(kw)
    return base


def test_mismatched_array_lengths_rejected():
    with pytest.raises(Exception, match="equal length"):
        ExerciseModel(**_ex(reps=[5, 5], rpe=[8]))


def test_all_empty_arrays_accepted():
    m = ExerciseModel(**_ex(reps=[], rpe=[], weight_kg=[]))
    assert m.reps == [] and m.rpe == [] and m.weight_kg == []


def test_omitted_arrays_padded_with_nulls():
    # FIXED (adversarial F4): an omitted array means "not tracked" and is
    # padded with None so everything stored is polars-explode-safe.
    m = ExerciseModel(**_ex(reps=[], rpe=[], weight_kg=[60.0]))
    assert m.reps == [None] and m.rpe == [None] and m.weight_kg == [60.0]


def test_partial_length_conflict_rejected():
    with pytest.raises(Exception, match="equal length"):
        ExerciseModel(**_ex(reps=[5, 5], weight_kg=[60.0]))


def test_rpe_above_10_rejected():
    # FIXED (adversarial F5): rpe is bounded 0..10 at the boundary.
    with pytest.raises(Exception):
        ExerciseModel(**_ex(rpe=[11]))


def test_infinite_weight_rejected():
    with pytest.raises(Exception):
        ExerciseModel(**_ex(weight_kg=[float("inf")]))


def test_sets_above_50_rejected():
    # A 1e9 `sets` poisoned every volume sum (adversarial F5); 50 is headroom.
    with pytest.raises(Exception):
        ExerciseModel(**_ex(sets=51))


def test_null_rpe_entries_allowed():
    m = ExerciseModel(**_ex(sets=2, reps=[5, 5], rpe=[None, 8], weight_kg=[60.0, 60.0]))
    assert m.rpe == [None, 8.0]


def test_sets_bounds():
    with pytest.raises(Exception):
        ExerciseModel(**_ex(sets=0))
    ExerciseModel(**_ex(sets=1))  # lower bound OK


def test_form_quality_bounds():
    for bad in (0, 6):
        with pytest.raises(Exception):
            ExerciseModel(**_ex(form_quality=bad))
    ExerciseModel(**_ex(form_quality=1))
    ExerciseModel(**_ex(form_quality=5))


def test_session_input_phase_optional_and_default_none():
    s = SessionInput(date=D, exercises=[ExerciseModel(**_ex())])
    assert s.phase is None
    assert s.pre_recovery_score is None


def test_session_input_rejects_bad_phase_vocab():
    with pytest.raises(Exception):
        SessionInput(date=D, phase="bulking", exercises=[])


def test_pre_recovery_score_bounds():
    for bad in (-1, 101):
        with pytest.raises(Exception):
            SessionInput(date=D, pre_recovery_score=bad, exercises=[])


def test_user_profile_defaults_are_coherent():
    p = UserProfile()
    assert p.equipment_access is None  # unasked ≠ full gym — the intake must see it missing
    assert p.goals == [] and p.priority_muscles == []


def test_availability_window_normalizes_human_times():
    # "7:00" and "07:00" must serialize identically — one canonical form.
    w = AvailabilityWindow(weekday=Weekday.fri, start="7:00", end=" 20:5 ")
    assert w.start == "07:00" and w.end == "20:05"


def test_availability_window_rejects_impossible_times():
    for bad in ("99:99", "24:00", "23:60", "-1:30", "19:00:00", "19h30", "19"):
        with pytest.raises(Exception, match="HH:MM|0-59|0-23"):
            AvailabilityWindow(weekday=Weekday.fri, start=bad)


def test_availability_window_allows_open_ended_slots():
    # "Fri after 7" with no known close: start-only is the intended encoding.
    w = AvailabilityWindow(weekday=Weekday.fri, start="19:00")
    assert w.start == "19:00" and w.end is None


# --- per-set arrays must match `sets` (P36) ----------------------------------------

@pytest.mark.parametrize("field, value", [
    ("reps", [5] * 5), ("rpe", [8] * 5), ("weight_kg", [100.0] * 5),
    ("reps", [5]), ("weight_kg", [100.0, 100.0, 100.0]),
])
def test_non_empty_per_set_array_must_have_exactly_sets_entries(field, value):
    with pytest.raises(Exception, match="sets"):
        ExerciseModel(name="Leg Press", sets=2, **{field: value})


def test_entered_weight_array_must_match_sets():
    with pytest.raises(Exception, match="sets"):
        ExerciseModel(name="Leg Press", sets=2, weight=[100, 100, 100, 100, 100], unit="kg")


def test_leg_press_sets_2_with_five_entries_is_rejected_at_the_tool():
    from coach_tools import DISPATCH, error_payload
    with pytest.raises(ValueError) as exc:
        DISPATCH["log_session"]({"date": "2026-10-01", "exercises": [
            {"name": "Leg Press", "sets": 2, "reps": [5] * 5, "weight_kg": [100] * 5}]})
    assert error_payload(exc.value)["error"] == "invalid_input"


def test_matching_and_empty_arrays_still_accepted_and_padded():
    m = ExerciseModel(name="Leg Press", sets=3, reps=[5, 5, 5])
    assert m.rpe == [None] * 3 and m.weight_kg == [None] * 3
    assert ExerciseModel(name="Leg Press", sets=3).reps == []
