"""An amend that changes nothing writes no audit entry and says so (P64a)."""

from __future__ import annotations

from datetime import date

from coach_tools import DISPATCH
from models import ExerciseModel, SessionInput
from skills.init import get_duckdb
from skills.session_logger import log_session

_FIELDS = ("index", "name", "sets", "reps", "rpe", "weight_kg", "load_type", "tempo",
           "form_quality", "pain_flag", "notes", "muscle_group")


def _log() -> str:
    conf = log_session(SessionInput(date=date(2026, 10, 4), exercises=[
        ExerciseModel(name="Meadows Row", sets=3, reps=[10, 9, 8], rpe=[7.3, 8.7, 9.1],
                      weight_kg=[30.0, 30.0, 30.0]),
        ExerciseModel(name="Dumbbell Fly", sets=2, reps=[12, 12], weight=[15, 15], unit="lb"),
    ], post_feedback="ok", pre_recovery_score=80))
    return str(conf.session_id)


def _audits() -> int:
    return get_duckdb().execute(
        "SELECT count(*) FROM decision_log WHERE event_type = 'session_amend'").fetchone()[0]


def _restate_all(sid: str) -> dict:
    d = DISPATCH["session_detail"]({"session_id": sid})["sessions"][0]
    return {"session_id": sid, "date": d["date"],
            "exercises": [{k: e[k] for k in _FIELDS} for e in d["exercises"]]}


def _row(sid: str):
    return get_duckdb().execute("SELECT * FROM sessions WHERE id = ?", [sid]).fetchone()


def test_an_amend_that_changes_nothing_writes_no_audit_and_says_changed_false():
    sid = _log()
    before = _row(sid)
    out = DISPATCH["session_amend"](_restate_all(sid))
    assert out["changed"] is False
    assert out["audit_id"] is None
    assert "nothing changed" in out["message"]
    assert _audits() == 0
    assert _row(sid) == before


def test_an_amend_that_changes_something_is_audited_and_says_changed_true():
    sid = _log()
    args = _restate_all(sid)
    args["exercises"][0]["reps"] = [10, 9, 7]
    out = DISPATCH["session_amend"](args)
    assert out["changed"] is True
    assert out["audit_id"]
    assert _audits() == 1


def test_changing_only_session_level_fields_counts_as_a_change():
    sid = _log()
    args = _restate_all(sid)
    args["post_feedback"] = "different"
    out = DISPATCH["session_amend"](args)
    assert out["changed"] is True and _audits() == 1


def test_a_noop_amend_leaves_a_following_real_amend_with_exactly_one_audit_entry():
    sid = _log()
    DISPATCH["session_amend"](_restate_all(sid))
    args = _restate_all(sid)
    args["date"] = "2026-10-03"
    DISPATCH["session_amend"](args)
    assert _audits() == 1
