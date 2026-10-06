"""session_template / session_labels (UX3)."""

from __future__ import annotations

from datetime import date

import pytest

from coach_tools import DISPATCH
from models import ExerciseModel, SessionInput
from skills.init import get_duckdb
from skills.session_logger import log_session
from skills.session_templates import session_labels, session_template


def _log(d, label, kind="training", **ex):
    base = dict(name="Lat Pulldown", sets=4, reps=[10] * 4, weight=[60] * 4, unit="lb",
                rpe=[8] * 4, notes="felt good", form_quality=4, pain_flag=False)
    base.update(ex)
    return log_session(SessionInput(date=d, label=label, kind=kind,
                                    exercises=[ExerciseModel(**base)])).session_id


def _habit(d, label="Squats"):
    return _log(d, label, kind="habit", name="Bodyweight Squat", sets=1, reps=[60],
                weight=[], unit=None, rpe=[], load_type="bodyweight")


def test_template_is_the_latest_session_with_that_label_normalized():
    _log(date(2026, 9, 28), "Back day", weight=[55] * 4)
    sid = _log(date(2026, 10, 2), "back-day")
    _log(date(2026, 10, 4), "Chest day")
    t = session_template(label="BACK  DAY")
    assert t.source_session_id == sid and t.source_date == date(2026, 10, 2)


def test_template_copies_loads_as_entered_and_never_per_day_fields():
    _log(date(2026, 10, 2), "Back day")
    ex = session_template(label="Back day").exercises[0]
    assert ex["weight"] == [60, 60, 60, 60] and ex["unit"] == "lb"
    assert ex["sets"] == 4 and ex["reps"] == [10, 10, 10, 10]
    for k in ("rpe", "notes", "form_quality", "pain_flag", "weight_kg"):
        assert k not in ex


def test_template_payload_logs_unchanged():
    _log(date(2026, 10, 2), "Back day")
    t = session_template(label="Back day")
    out = DISPATCH["log_session"]({"date": "2026-10-05", "label": t.label, "kind": t.kind,
                                   "exercises": t.exercises})
    assert "error" not in out


def test_bodyweight_exercise_has_no_weight_or_unit():
    _habit(date(2026, 10, 3))
    ex = session_template(label="squats").exercises[0]
    assert "weight" not in ex and "unit" not in ex and ex["load_type"] == "bodyweight"
    assert session_template(label="squats").kind == "habit"


def test_template_by_session_id_and_arg_rules():
    sid = _log(date(2026, 10, 2), None)
    assert session_template(session_id=str(sid)).label is None
    with pytest.raises(ValueError):
        session_template()
    with pytest.raises(ValueError):
        session_template(label="x", session_id=str(sid))


def test_unknown_label_lists_known_labels():
    _log(date(2026, 10, 2), "Back day")
    with pytest.raises(ValueError, match="Back day"):
        session_template(label="Leg day")


def test_legacy_row_without_units_and_nameless_entry_still_templates():
    get_duckdb().execute(
        "INSERT INTO sessions (date, phase, kind, label, exercises) VALUES "
        "(DATE '2026-09-01', 'accumulation', 'training', 'Old', "
        "[{'name': 'Lat Pulldown', 'sets': 2, 'reps': [10.0, 10.0], 'weight_kg': [27.5, 27.5]}, "
        " {'name': NULL, 'sets': 1}])")
    t = session_template(label="Old")
    assert t.exercises[0]["weight"] == [27.5, 27.5] and t.exercises[0]["unit"] == "kg"
    assert len(t.exercises) == 1 and len(t.skipped) == 1
    out = DISPATCH["log_session"]({"date": "2026-10-05", "exercises": t.exercises})
    assert "error" not in out


def test_labels_group_by_normalized_label_with_gap():
    _log(date(2026, 10, 1), "Back day")
    _log(date(2026, 10, 3), "back day")
    _habit(date(2026, 10, 6))
    rows = {r.label.lower(): r for r in session_labels(today=date(2026, 10, 6))}
    back = rows["back day"]
    assert back.count == 2 and back.last_date == date(2026, 10, 3)
    assert back.days_without_entry == 3 and back.kind == "training"
    assert rows["squats"].days_without_entry == 0 and rows["squats"].kind == "habit"


def test_labels_empty_when_nothing_is_labeled():
    _log(date(2026, 10, 2), None)
    assert session_labels(today=date(2026, 10, 6)) == []
