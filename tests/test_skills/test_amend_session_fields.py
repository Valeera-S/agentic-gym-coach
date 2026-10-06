"""P79: amend session-level fields without restating date / exercises."""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from coach_tools import DISPATCH
from models import ExerciseModel, SessionInput
from skills.init import get_duckdb
from skills.session_logger import log_session
from skills.sessions import amend_session, parse_amend_input, restore_snapshot

D = date(2026, 10, 2)


def _log():
    return log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="Lat Pulldown", sets=2, reps=[10, 9], weight=[60, 62.5], unit="lb",
                      notes="felt smooth", load_type="machine_stack"),
        ExerciseModel(name="Zzz Weird Machine", sets=1, reps=[12], weight_kg=[30])])).session_id


def _detail(sid):
    return DISPATCH["session_detail"]({"session_id": str(sid)})["sessions"][0]


def _audits():
    return get_duckdb().execute(
        "SELECT count(*) FROM decision_log WHERE event_type = 'session_amend'").fetchone()[0]


def test_label_only_amend_keeps_exercises_identical_and_audits_once():
    sid = _log()
    before = _detail(sid)
    out = DISPATCH["session_amend"]({"session_id": str(sid), "label": "Back day"})
    assert out["changed"] is True and out["audit_id"]
    after = _detail(sid)
    assert after["exercises"] == before["exercises"]
    assert after["date"] == before["date"]
    assert after["label"] == "Back day"
    assert _audits() == 1


def test_post_feedback_only_amend():
    sid = _log()
    before = _detail(sid)
    DISPATCH["session_amend"]({"session_id": str(sid), "post_feedback": "good"})
    after = _detail(sid)
    assert after["post_feedback"] == "good" and after["exercises"] == before["exercises"]


def test_date_only_amend_keeps_exercises():
    sid = _log()
    before = _detail(sid)
    DISPATCH["session_amend"]({"session_id": str(sid), "date": "2026-10-01"})
    after = _detail(sid)
    assert after["date"] == "2026-10-01" and after["exercises"] == before["exercises"]


def test_explicit_nulls_mean_keep():
    sid = _log()
    out = DISPATCH["session_amend"]({"session_id": str(sid), "date": None, "exercises": None})
    assert out["changed"] is False


def test_omitted_everything_is_a_noop_without_audit():
    sid = _log()
    out = DISPATCH["session_amend"]({"session_id": str(sid)})
    assert out["changed"] is False and out["audit_id"] is None
    assert _audits() == 0


def test_empty_exercise_list_is_still_invalid():
    sid = _log()
    with pytest.raises(ValueError):
        DISPATCH["session_amend"]({"session_id": str(sid), "exercises": []})


def test_restore_of_a_label_only_amend_brings_the_old_label_back():
    sid = _log()
    DISPATCH["session_amend"]({"session_id": str(sid), "label": "Old"})
    audit = DISPATCH["session_amend"]({"session_id": str(sid), "label": "New"})["audit_id"]
    assert _detail(sid)["label"] == "New"
    restore_snapshot(audit)
    assert _detail(sid)["label"] == "Old"


def test_skill_layer_accepts_only_session_id_and_label():
    sid = _log()
    out = amend_session(parse_amend_input({"session_id": str(sid), "label": "Pull"}))
    assert out.changed and out.label == "Pull"


def test_legacy_row_can_get_a_label_without_revalidation():
    bad = {"name": "Bench Press", "raw_name": "Bench Press", "muscle_source": "catalog",
           "muscle_group": "chest", "sets": 2, "reps": [8.0, 8.0], "rpe": [0.0, 8.0],
           "weight_kg": [60.0, 60.0], "tempo": None, "form_quality": 0, "pain_flag": False,
           "notes": None, "load_type": "total", "entered_weight": None, "entered_unit": None}
    sid = get_duckdb().execute(
        "INSERT INTO sessions (date, phase, pre_recovery_score, exercises, post_feedback, kind) "
        "VALUES (?, 'maintenance', NULL, ?, NULL, 'training') RETURNING CAST(id AS VARCHAR)",
        [date(1999, 12, 31), [bad]]).fetchone()[0]
    out = DISPATCH["session_amend"]({"session_id": sid, "label": "Legacy"})
    assert out["changed"] is True
    row = get_duckdb().execute(
        "SELECT date, exercises, label FROM sessions WHERE id = ?", [sid]).fetchone()
    assert row[0] == date(1999, 12, 31) and row[1] == [bad] and row[2] == "Legacy"


def test_mcp_wrapper_accepts_session_id_and_label_only():
    import mcp_server
    sid = _log()
    res = asyncio.run(mcp_server.mcp.call_tool(
        "coach_session_amend", {"session_id": str(sid), "label": "Via MCP"}))
    assert res.is_error is False
    assert json.loads(res.content[0].text)["label"] == "Via MCP"
    tools = {t.name: t.input_schema for t in asyncio.run(mcp_server.mcp.list_tools())}
    props = tools["coach_session_amend"]["properties"]
    assert "date" in props and "exercises" in props
    assert tools["coach_session_amend"].get("required") == ["session_id"]
