"""coach_session_amend / coach_session_delete — corrections with a full audit.

Before these tools a wrong entry (e.g. a needs_review guess) could neither be
fixed nor removed through any supported path. Both write the complete
pre-change row to the decision audit trail, restorable from that entry alone.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

import mcp_server
from coach_tools import DISPATCH, error_payload
from models import ExerciseModel, SessionInput
from skills.init import get_duckdb
from skills.session_logger import log_session
from skills.sessions import restore_snapshot

D = date(2026, 10, 4)


def _log(**kw) -> str:
    conf = log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="Meadows Row", sets=3, reps=[10, 9, 8], rpe=[7.3, 8.7, 9.1],
                      weight_kg=[30.0, 30.0, 30.0]),
        ExerciseModel(name="Dumbbell Fly", sets=2, reps=[12, 12], weight=[15, 15], unit="lb"),
    ], post_feedback="ok", pre_recovery_score=80, **kw))
    return str(conf.session_id)


def _row(sid: str):
    return get_duckdb().execute("SELECT * FROM sessions WHERE id = ?", [sid]).fetchone()


def _audit_rows():
    return get_duckdb().execute(
        "SELECT id, event_type, trigger_signal, payload FROM decision_log "
        "WHERE event_type LIKE 'session_%' ORDER BY created_at").fetchall()


def _detail(sid: str) -> dict:
    return DISPATCH["session_detail"]({"session_id": sid})["sessions"][0]


# --- amend ------------------------------------------------------------------------

def test_amend_revalidates_and_recanonicalizes_like_a_fresh_log_and_keeps_the_id():
    sid = _log()
    before = _detail(sid)
    out = DISPATCH["session_amend"]({
        "session_id": sid, "date": "2026-10-05",
        "exercises": [{"name": "chest-supported db row", "sets": 3, "reps": [10, 10, 10],
                       "weight": [40, 40, 40], "unit": "lb"},
                      {"name": "Zercher Carry", "sets": 1, "reps": [30]}],
        "post_feedback": "corrected"})
    assert out["action"] == "amended" and out["session_id"] == sid
    after = _detail(sid)
    assert after["id"] == sid and after["date"] == "2026-10-05"
    assert after["created_at"] == before["created_at"]
    row, carry = after["exercises"]
    assert (row["name"], row["raw_name"]) == ("Chest-Supported Dumbbell Row", "chest-supported db row")
    assert (row["muscle_source"], row["load_type"], row["unit_as_entered"]) == ("catalog", "per_hand", "lb")
    assert carry["muscle_group"] == "unclassified"
    # the same anomaly flags a fresh log would raise
    assert [f["code"] for f in out["anomaly_flags"]] == ["needs_review"]
    assert after["post_feedback"] == "corrected"
    assert after["pre_recovery_score"] == 80  # omitted: kept


def test_amend_never_silently_clears_feedback_or_recovery_score():
    """The natural correction call sends only id, date and exercises."""
    sid = _log()
    DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04",
                               "exercises": [{"name": "Dumbbell Bench Press", "sets": 3}]})
    s = _detail(sid)
    assert (s["post_feedback"], s["pre_recovery_score"]) == ("ok", 80)
    DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "post_feedback": "new",
                               "pre_recovery_score": 55,
                               "exercises": [{"name": "Dumbbell Bench Press", "sets": 3}]})
    s = _detail(sid)
    assert (s["post_feedback"], s["pre_recovery_score"]) == ("new", 55)


def test_explicit_null_keeps_and_clear_removes():
    sid = _log()
    out = mcp_server.coach_session_amend(session_id=sid, date="2026-10-04",
                                         exercises=[{"name": "Squat", "sets": 1}],
                                         post_feedback=None, pre_recovery_score=None)
    assert (out["post_feedback"], out["pre_recovery_score"]) == ("ok", 80)
    out = mcp_server.coach_session_amend(session_id=sid, date="2026-10-04",
                                         exercises=[{"name": "Squat", "sets": 1}],
                                         clear=["pre_recovery_score"])
    assert (out["post_feedback"], out["pre_recovery_score"]) == ("ok", None)
    s = _detail(sid)
    assert (s["post_feedback"], s["pre_recovery_score"]) == ("ok", None)
    DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "clear": ["post_feedback"],
                               "exercises": [{"name": "Squat", "sets": 1}]})
    assert _detail(sid)["post_feedback"] is None


@pytest.mark.parametrize("bad", [
    {"clear": ["phase"]},                                    # not clearable
    {"clear": ["pre_recovery_score"], "pre_recovery_score": 70},  # both clear and set
    {"clear": "post_feedback"},
])
def test_bad_clear_requests_are_rejected_before_any_write(bad):
    sid = _log()
    before_row = _row(sid)
    with pytest.raises(ValidationError):
        DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04",
                                   "exercises": [{"name": "Squat", "sets": 1}], **bad})
    assert _row(sid) == before_row and _audit_rows() == []


def test_amend_keeps_stored_phase_and_kind_unless_given():
    sid = _log(kind="habit", phase="deload")
    DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04",
                               "exercises": [{"name": "Bodyweight Squat", "sets": 3}]})
    s = _detail(sid)
    assert (s["kind"], s["phase"]) == ("habit", "deload")
    DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "kind": "training",
                               "phase": "accumulation", "exercises": [{"name": "Squat", "sets": 3}]})
    s = _detail(sid)
    assert (s["kind"], s["phase"]) == ("training", "accumulation")


@pytest.mark.parametrize("bad", [
    {"exercises": [{"name": "Squat", "sets": 0}]},                       # sets out of range
    {"exercises": [{"name": "Squat", "sets": 2, "reps": [5], "rpe": [8, 9]}]},  # array mismatch
    {"exercises": [{"name": "Squat", "sets": 1, "weight": [100], "weight_kg": [1]}], },  # both forms disagree
    {"exercises": [{"name": "Squat", "sets": 1}], "kind": "rest"},
    {"exercises": [{"name": "Squat", "sets": 1}], "date": "not a date"},
    {"exercises": "nope"},
])
def test_malformed_amend_is_rejected_before_any_write(bad):
    sid = _log()
    before_row, before_audit = _row(sid), _audit_rows()
    payload = {"session_id": sid, "date": "2026-10-04", **bad}
    with pytest.raises(Exception) as exc:
        DISPATCH["session_amend"](payload)
    assert error_payload(exc.value)["error"] == "invalid_input"
    assert _row(sid) == before_row and _audit_rows() == before_audit


@pytest.mark.parametrize("sid", ["00000000-0000-0000-0000-000000000000", "not-a-uuid", 5])
def test_amend_and_delete_of_an_unknown_id_are_invalid_input(sid):
    for cmd, args in (("session_amend", {"session_id": sid, "date": "2026-10-04",
                                         "exercises": [{"name": "Squat", "sets": 1}]}),
                      ("session_delete", {"session_id": sid})):
        with pytest.raises(Exception) as exc:
            DISPATCH[cmd](args)
        assert error_payload(exc.value)["error"] == "invalid_input"
    assert _audit_rows() == []


def test_amend_audit_entry_reconstructs_the_previous_row_exactly():
    sid = _log()
    original = _row(sid)
    out = DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04",
                                     "exercises": [{"name": "Squat", "sets": 1}]})
    (audit_id, event, signal, payload), = _audit_rows()
    assert str(audit_id) == out["audit_id"] and event == "session_amend"
    assert signal == f"session {sid} amended"
    snap = json.loads(payload)
    assert snap["before"]["id"] == sid and snap["after"]["exercises"][0]["name"] == "Squat"
    restore_snapshot(audit_id)
    assert _row(sid) == original  # every column, float32 reps/rpe included


# --- delete ------------------------------------------------------------------------

def test_delete_removes_the_row_and_the_audit_entry_alone_restores_it():
    sid = _log()
    original = _row(sid)
    out = DISPATCH["session_delete"]({"session_id": sid})
    assert out["action"] == "deleted"
    assert _row(sid) is None
    (audit_id, event, signal, payload), = _audit_rows()
    assert event == "session_delete" and signal == f"session {sid} deleted"
    assert json.loads(payload)["before"]["exercises"][1]["entered_unit"] == "lb"
    restore_snapshot(audit_id)
    assert _row(sid) == original


def test_delete_affects_only_its_own_session():
    keep, drop = _log(), _log()
    DISPATCH["session_delete"]({"session_id": drop})
    assert _row(keep) is not None
    assert [str(s["id"]) for s in DISPATCH["sessions"]({})] == [keep]


def test_deleting_twice_is_invalid_input_and_audits_once():
    sid = _log()
    DISPATCH["session_delete"]({"session_id": sid})
    with pytest.raises(ValueError):
        DISPATCH["session_delete"]({"session_id": sid})
    assert len(_audit_rows()) == 1


def test_a_failure_mid_change_rolls_back_both_the_change_and_the_audit(monkeypatch):
    import skills.sessions as sessions_skill
    sid = _log()
    original = _row(sid)

    def boom(*a, **k):
        raise RuntimeError("audit write failed")
    monkeypatch.setattr(sessions_skill, "_audit", boom)
    with pytest.raises(RuntimeError):
        DISPATCH["session_delete"]({"session_id": sid})
    assert _row(sid) == original and _audit_rows() == []


# --- surfaces ----------------------------------------------------------------------

def test_mcp_wrappers_match_the_cli():
    sid = _log()
    out = mcp_server.coach_session_amend(session_id=sid, date="2026-10-04",
                                         exercises=[{"name": "Lat Pulldown", "sets": 3}])
    assert out["action"] == "amended"
    assert mcp_server.coach_session_amend(session_id=sid, date="2026-10-04",
                                          exercises=[])["action"] == "amended"
    assert mcp_server.coach_session_delete(session_id=sid)["action"] == "deleted"
    assert mcp_server.coach_session_delete(session_id=sid)["error"] == "invalid_input"


def test_persona_requires_confirmation_before_delete_and_amend():
    from pathlib import Path
    text = (Path(mcp_server.__file__).parent / "docs" / "COACH_PROMPT.md").read_text(encoding="utf-8")
    assert "coach_session_delete" in text and "coach_session_amend" in text
    assert "explicit yes" in text
