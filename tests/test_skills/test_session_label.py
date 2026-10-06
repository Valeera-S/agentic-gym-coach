"""Session labels on every write/read path (UX3)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from coach_tools import DISPATCH
from models import ExerciseModel, SessionInput
from skills.session_logger import log_session
from skills.sessions import (amend_session, delete_session, get_session_detail, list_sessions,
                             parse_amend_input, restore_snapshot)

D = date(2026, 10, 2)


def _log(label=None, d=D):
    return log_session(SessionInput(date=d, label=label, exercises=[ExerciseModel(
        name="Lat Pulldown", sets=4, reps=[10] * 4, weight=[60] * 4, unit="lb")]))


def test_label_round_trips_through_log_detail_and_list():
    sid = _log("Back day").session_id
    assert get_session_detail(session_id=sid)[0].label == "Back day"
    assert list_sessions(10)[0]["label"] == "Back day"


def test_label_is_optional():
    sid = _log().session_id
    assert get_session_detail(session_id=sid)[0].label is None
    assert list_sessions(10)[0]["label"] is None


@pytest.mark.parametrize("bad", ["", "   ", "a" * 41, "back\nday", "back\tday"])
def test_bad_labels_are_rejected(bad):
    with pytest.raises(ValidationError):
        SessionInput(date=D, label=bad, exercises=[ExerciseModel(name="Lat Pulldown", sets=1)])


def test_a_40_character_label_is_fine():
    assert _log("x" * 40).session_id


def _amend_args(sid, **extra):
    return {"session_id": str(sid), "date": D.isoformat(),
            "exercises": [{"index": 0, "name": "Lat Pulldown", "sets": 4, "reps": [10] * 4,
                           "weight": [65] * 4, "unit": "lb"}], **extra}


def test_amend_without_label_keeps_it():
    sid = _log("Back day").session_id
    amend_session(parse_amend_input(_amend_args(sid)))
    assert get_session_detail(session_id=sid)[0].label == "Back day"


def test_amend_sets_and_clears_label():
    sid = _log().session_id
    out = amend_session(parse_amend_input(_amend_args(sid, label="Back day")))
    assert out.label == "Back day" and get_session_detail(session_id=sid)[0].label == "Back day"
    amend_session(parse_amend_input(_amend_args(sid, clear=["label"])))
    assert get_session_detail(session_id=sid)[0].label is None


def test_clear_and_value_together_is_rejected():
    sid = _log("Back day").session_id
    with pytest.raises((ValidationError, ValueError)):
        parse_amend_input(_amend_args(sid, label="X", clear=["label"]))


def test_restore_after_delete_brings_the_label_back():
    sid = _log("Back day").session_id
    audit = delete_session(sid).audit_id
    restore_snapshot(audit)
    assert get_session_detail(session_id=sid)[0].label == "Back day"


def test_restore_of_an_amend_brings_the_old_label_back():
    sid = _log("Back day").session_id
    audit = amend_session(parse_amend_input(_amend_args(sid, label="Pull day"))).audit_id
    assert get_session_detail(session_id=sid)[0].label == "Pull day"
    restore_snapshot(audit)
    assert get_session_detail(session_id=sid)[0].label == "Back day"


def test_cli_accepts_label_on_log_and_amend():
    out = DISPATCH["log_session"]({"date": D.isoformat(), "label": "Back day", "exercises": [
        {"name": "Lat Pulldown", "sets": 1, "reps": [10]}]})
    assert "error" not in out
    sid = out["session_id"]
    out = DISPATCH["session_amend"]({"session_id": sid, "date": D.isoformat(), "label": "Pull",
                                     "exercises": [{"index": 0, "name": "Lat Pulldown",
                                                    "sets": 1, "reps": [10]}]})
    assert "error" not in out and out["label"] == "Pull"
