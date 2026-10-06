"""Legacy rows round-trip detail -> amend unchanged (P58).

Rows written by older code can hold values today's input rules reject. The
coach must still be able to fix ANOTHER exercise of such a session, so an
exercise restated unchanged keeps its stored values and skips the input rules;
a changed or new exercise is validated in full.
"""

from __future__ import annotations

from datetime import date

import pytest

from coach_tools import DISPATCH, error_payload
from skills.init import get_duckdb

_FIELDS = ("index", "name", "sets", "reps", "rpe", "weight_kg", "load_type", "tempo",
           "form_quality", "pain_flag", "notes", "muscle_group")


def _struct(**kw) -> dict:
    base = {"name": "Bench Press", "raw_name": "Bench Press", "muscle_source": "catalog",
            "muscle_group": "chest", "sets": 2, "reps": [8.0, 8.0], "rpe": [7.0, 8.0],
            "weight_kg": [60.0, 60.0], "tempo": None, "form_quality": 5, "pain_flag": False,
            "notes": None, "load_type": "total", "entered_weight": None, "entered_unit": None}
    base.update(kw)
    return base


PLAIN = _struct(name="Squat", raw_name="Squat", muscle_group="quads",
                sets=1, reps=[5.0], rpe=[8.0], weight_kg=[100.0])

SHAPES = {
    "zero_rpe": _struct(rpe=[0.0, 8.0]),
    "zero_reps": _struct(reps=[0.0, 8.0]),
    "zero_weight": _struct(weight_kg=[0.0, 60.0]),
    "arrays_longer_than_sets": _struct(sets=2, reps=[8.0, 8.0, 8.0], rpe=[7.0, 7.0, 7.0],
                                       weight_kg=[60.0, 60.0, 60.0]),
    "sets_null": _struct(sets=None),
    "sets_zero": _struct(sets=0, reps=[], rpe=[], weight_kg=[]),
    "sets_sixty": _struct(sets=60, reps=[], rpe=[], weight_kg=[]),
    "form_quality_zero": _struct(form_quality=0),
    "form_quality_null_pain_null": _struct(form_quality=None, pain_flag=None),
    "long_name": _struct(name="X" * 300, raw_name="X" * 300, muscle_source="keyword"),
    "control_char_name": _struct(name="Bench\x07Press", raw_name="Bench\x07Press",
                                 muscle_source="keyword"),
    "weight_3000": _struct(weight_kg=[3000.0, 60.0]),
    "reps_150": _struct(reps=[150.0, 8.0]),
    "tempo_newline": _struct(tempo="3-1\n-1"),
}


def _insert(exercises: list[dict], d: date = date(2026, 10, 1)) -> str:
    row = get_duckdb().execute(
        "INSERT INTO sessions (date, phase, pre_recovery_score, exercises, post_feedback, kind) "
        "VALUES (?, 'maintenance', NULL, ?, NULL, 'training') RETURNING CAST(id AS VARCHAR)",
        [d, exercises]).fetchone()
    return row[0]


def _stored(sid: str):
    return get_duckdb().execute("SELECT date, exercises FROM sessions WHERE id = ?", [sid]).fetchone()


def _restate(detail: dict, i: int) -> dict:
    return {k: detail["exercises"][i][k] for k in _FIELDS}


def _detail(sid: str) -> dict:
    return DISPATCH["session_detail"]({"session_id": sid})["sessions"][0]


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_legacy_exercise_round_trips_detail_to_amend_unchanged(shape):
    sid = _insert([SHAPES[shape], PLAIN])
    before = _stored(sid)
    d = _detail(sid)
    out = DISPATCH["session_amend"]({
        "session_id": sid, "date": d["date"],
        "exercises": [_restate(d, 0), _restate(d, 1)]})
    assert out["action"] == "amended"
    assert _stored(sid) == before


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_editing_another_exercise_of_a_legacy_session_succeeds(shape):
    sid = _insert([SHAPES[shape], PLAIN])
    bad_before = _stored(sid)[1][0]
    d = _detail(sid)
    fixed = _restate(d, 1)
    fixed["reps"] = [6]
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                               "exercises": [_restate(d, 0), fixed]})
    after = _stored(sid)[1]
    assert after[0] == bad_before                 # the bad exercise is untouched
    assert after[1]["reps"] == [6.0]              # the other one was corrected


def test_a_legacy_session_date_before_2000_survives_an_unchanged_restate():
    sid = _insert([PLAIN], d=date(1999, 12, 31))
    d = _detail(sid)
    DISPATCH["session_amend"]({"session_id": sid, "date": "1999-12-31",
                               "exercises": [_restate(d, 0)], "post_feedback": "note"})
    assert _stored(sid)[0] == date(1999, 12, 31)
    with pytest.raises(ValueError):               # a CHANGED date is still checked
        DISPATCH["session_amend"]({"session_id": sid, "date": "1998-01-01",
                                   "exercises": [_restate(d, 0)]})


@pytest.mark.parametrize("shape, field, value", [
    ("zero_rpe", "rpe", [0.0, 7.0]),
    ("sets_sixty", "sets", 61),
    ("long_name", "name", "Y" * 300),
    ("weight_3000", "weight_kg", [3000.0, 61.0]),
    ("tempo_newline", "tempo", "3-1\n-2"),
    ("form_quality_zero", "form_quality", 6),
])
def test_editing_the_bad_exercise_itself_is_still_validated(shape, field, value):
    sid = _insert([SHAPES[shape], PLAIN])
    before = _stored(sid)
    d = _detail(sid)
    ex = _restate(d, 0)
    ex[field] = value
    with pytest.raises(ValueError):
        DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                                   "exercises": [ex, _restate(d, 1)]})
    assert _stored(sid) == before


def test_a_new_exercise_is_fully_validated_next_to_a_legacy_one():
    sid = _insert([SHAPES["zero_rpe"]])
    d = _detail(sid)
    with pytest.raises(ValueError):
        DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": [
            _restate(d, 0), {"new": True, "name": "Squat", "sets": 1, "rpe": [0]}]})


def test_unchanged_legacy_exercise_still_raises_its_anomaly_flags():
    sid = _insert([SHAPES["form_quality_zero"], PLAIN])
    d = _detail(sid)
    out = DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                                     "exercises": [_restate(d, 0), _restate(d, 1)]})
    assert [f["code"] for f in out["anomaly_flags"]] == ["form_quality_low"]


# --- explicit null on input (reps / rpe / weight_kg / form_quality / pain_flag) ---------

def test_explicit_null_arrays_mean_not_recorded_on_a_fresh_log():
    out = DISPATCH["log_session"]({"date": "2026-10-01", "exercises": [
        {"name": "Pull-Up", "sets": 2, "reps": None, "rpe": None, "weight_kg": None}]})
    ex = _detail(out["session_id"])["exercises"][0]
    assert ex["reps"] == [None, None] and ex["weight_kg"] == [None, None]


def test_explicit_null_form_quality_and_pain_flag_use_the_default_on_a_new_exercise():
    out = DISPATCH["log_session"]({"date": "2026-10-01", "exercises": [
        {"name": "Pull-Up", "sets": 1, "form_quality": None, "pain_flag": None}]})
    ex = _detail(out["session_id"])["exercises"][0]
    assert (ex["form_quality"], ex["pain_flag"]) == (5, False)


def test_null_form_quality_and_pain_flag_keep_the_stored_values_on_an_unchanged_restatement():
    sid = _insert([_struct(form_quality=3, pain_flag=True), PLAIN])
    d = _detail(sid)
    ex = _restate(d, 0)
    ex.update(form_quality=None, pain_flag=None)
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                               "exercises": [ex, _restate(d, 1)]})
    got = _stored(sid)[1][0]
    assert (got["form_quality"], got["pain_flag"]) == (3, True)


def test_null_form_quality_on_a_changed_exercise_uses_the_default():
    sid = _insert([_struct(form_quality=3), PLAIN])
    d = _detail(sid)
    ex = _restate(d, 0)
    ex.update(form_quality=None, reps=[9, 9])
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                               "exercises": [ex, _restate(d, 1)]})
    assert _stored(sid)[1][0]["form_quality"] == 5
