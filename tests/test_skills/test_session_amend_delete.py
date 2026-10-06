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
        "exercises": [{"new": True, "name": "chest-supported db row", "sets": 3, "reps": [10, 10, 10],
                       "weight": [40, 40, 40], "unit": "lb"},
                      {"new": True, "name": "Zercher Carry", "sets": 1, "reps": [30]}],
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
                               "exercises": [{"new": True, "name": "Dumbbell Bench Press", "sets": 3}]})
    s = _detail(sid)
    assert (s["post_feedback"], s["pre_recovery_score"]) == ("ok", 80)
    DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "post_feedback": "new",
                               "pre_recovery_score": 55,
                               "exercises": [{"new": True, "name": "Dumbbell Bench Press", "sets": 3}]})
    s = _detail(sid)
    assert (s["post_feedback"], s["pre_recovery_score"]) == ("new", 55)


def test_explicit_null_keeps_and_clear_removes():
    sid = _log()
    out = mcp_server.coach_session_amend(session_id=sid, date="2026-10-04",
                                         exercises=[{"new": True, "name": "Squat", "sets": 1}],
                                         post_feedback=None, pre_recovery_score=None)
    assert (out["post_feedback"], out["pre_recovery_score"]) == ("ok", 80)
    out = mcp_server.coach_session_amend(session_id=sid, date="2026-10-04",
                                         exercises=[{"new": True, "name": "Squat", "sets": 1}],
                                         clear=["pre_recovery_score"])
    assert (out["post_feedback"], out["pre_recovery_score"]) == ("ok", None)
    s = _detail(sid)
    assert (s["post_feedback"], s["pre_recovery_score"]) == ("ok", None)
    DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "clear": ["post_feedback"],
                               "exercises": [{"new": True, "name": "Squat", "sets": 1}]})
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
                                   "exercises": [{"new": True, "name": "Squat", "sets": 1}], **bad})
    assert _row(sid) == before_row and _audit_rows() == []


def test_amend_keeps_stored_phase_and_kind_unless_given():
    sid = _log(kind="habit", phase="deload")
    DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04",
                               "exercises": [{"new": True, "name": "Bodyweight Squat", "sets": 3}]})
    s = _detail(sid)
    assert (s["kind"], s["phase"]) == ("habit", "deload")
    DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "kind": "training",
                               "phase": "accumulation", "exercises": [{"new": True, "name": "Squat", "sets": 3}]})
    s = _detail(sid)
    assert (s["kind"], s["phase"]) == ("training", "accumulation")


@pytest.mark.parametrize("bad", [
    {"exercises": [{"new": True, "name": "Squat", "sets": 0}]},                       # sets out of range
    {"exercises": [{"new": True, "name": "Squat", "sets": 2, "reps": [5], "rpe": [8, 9]}]},  # array mismatch
    {"exercises": [{"new": True, "name": "Squat", "sets": 1, "weight": [100], "weight_kg": [1]}], },  # both forms disagree
    {"exercises": [{"new": True, "name": "Squat", "sets": 1}], "kind": "rest"},
    {"exercises": [{"new": True, "name": "Squat", "sets": 1}], "date": "not a date"},
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
                                         "exercises": [{"new": True, "name": "Squat", "sets": 1}]}),
                      ("session_delete", {"session_id": sid})):
        with pytest.raises(Exception) as exc:
            DISPATCH[cmd](args)
        assert error_payload(exc.value)["error"] == "invalid_input"
    assert _audit_rows() == []


def test_amend_audit_entry_reconstructs_the_previous_row_exactly():
    sid = _log()
    original = _row(sid)
    out = DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04",
                                     "exercises": [{"new": True, "name": "Squat", "sets": 1}]})
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
                                         exercises=[{"new": True, "name": "Lat Pulldown", "sets": 3}])
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


# --- amend built from read-back (holistic pass: provenance must not be rewritten) ---

_INPUT_KEYS = ("index", "name", "muscle_group", "sets", "reps", "rpe", "weight_kg", "load_type",
               "tempo", "form_quality", "pain_flag", "notes")
_PROVENANCE = ("name", "raw_name", "muscle_group", "muscle_source", "needs_review",
               "load_type", "weight_kg", "entered_weight", "entered_unit")


def _as_input(ex: dict) -> dict:
    return {k: ex[k] for k in _INPUT_KEYS if ex.get(k) is not None}


def _log_typed() -> str:
    conf = log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="lat pulldown", sets=2, reps=[10, 10], weight=[60, 60], unit="lb"),
        ExerciseModel(name="Barbell Bench Press", sets=2, reps=[5, 5], weight_kg=[80.0, 80.0]),
        ExerciseModel(name="Tate Pres", sets=2, reps=[12, 12]),
    ]))
    return str(conf.session_id)


def test_echoing_the_read_back_shape_is_rejected_naming_the_field_and_writes_nothing():
    sid = _log_typed()
    d = _detail(sid)
    with pytest.raises(ValidationError) as e:
        DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                                   "exercises": d["exercises"]})
    msg = str(e.value)
    assert "read-back" in msg and "`raw_name`" in msg and "`entered_weight`" in msg
    assert error_payload(e.value)["error"] == "invalid_input"
    assert _detail(sid) == d and _audit_rows() == []


def test_an_unknown_amend_exercise_key_is_rejected():
    sid = _log_typed()
    with pytest.raises(ValidationError):
        DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04",
                                   "exercises": [{"new": True, "name": "Pull-Up", "sets": 3, "weight_lb": [0]}]})


def test_an_unchanged_amend_built_from_read_back_keeps_every_provenance_field():
    sid = _log_typed()
    d = _detail(sid)
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                               "exercises": [_as_input(x) for x in d["exercises"]]})
    after = _detail(sid)
    for b, a in zip(d["exercises"], after["exercises"], strict=True):
        assert {k: a[k] for k in _PROVENANCE} == {k: b[k] for k in _PROVENANCE}
    pulldown = after["exercises"][0]
    assert (pulldown["raw_name"], pulldown["entered_unit"], pulldown["entered_weight"]) == \
        ("lat pulldown", "lb", [60.0, 60.0])
    assert after["exercises"][2]["needs_review"] is True  # the guess is still flagged


def test_fixing_one_exercise_leaves_the_others_provenance_alone():
    sid = _log_typed()
    d = _detail(sid)
    exs = [_as_input(x) for x in d["exercises"]]
    exs[2] = {"new": True, "name": "Close-Grip Bench Press", "sets": 2, "reps": [12, 12]}
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": exs})
    after = _detail(sid)["exercises"]
    assert after[0]["muscle_source"] == after[1]["muscle_source"] == "catalog"
    assert after[0]["entered_unit"] == "lb"
    assert (after[2]["name"], after[2]["muscle_source"], after[2]["needs_review"]) == \
        ("Close-Grip Bench Press", "catalog", False)


def test_changed_values_still_go_through_as_changes():
    sid = _log_typed()
    d = _detail(sid)
    exs = [_as_input(x) for x in d["exercises"]]
    exs[0]["weight_kg"] = [30.0, 30.0]           # a corrected load, now in kg
    exs[1]["muscle_group"] = "triceps"           # a deliberate override
    exs[2]["muscle_group"] = "triceps"           # the user confirms the muscle
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": exs})
    after = _detail(sid)["exercises"]
    assert (after[0]["weight_kg"], after[0]["entered_unit"]) == ([30.0, 30.0], None)
    assert (after[1]["muscle_group"], after[1]["muscle_source"]) == ("triceps", "caller")
    assert (after[2]["muscle_group"], after[2]["muscle_source"]) == ("triceps", "caller")
    assert after[2]["needs_review"] is True  # an unknown name stays flagged


def test_a_stored_caller_override_survives_an_unchanged_amend():
    conf = log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="Barbell Bench Press", muscle_group="triceps", sets=1, reps=[5])]))
    sid = str(conf.session_id)
    d = _detail(sid)
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                               "exercises": [_as_input(x) for x in d["exercises"]]})
    ex = _detail(sid)["exercises"][0]
    assert (ex["muscle_group"], ex["muscle_source"]) == ("triceps", "caller")


def _legacy_session(*exercises: dict) -> str:
    """A pre-0003-shaped row: no raw_name / muscle_source / load_type stored."""
    template = get_duckdb().execute(
        "SELECT exercises FROM sessions WHERE id = ?", [_log_typed()]).fetchone()[0][1]
    get_duckdb().execute("DELETE FROM sessions")
    structs = [dict(template, raw_name=None, muscle_source=None, load_type=None,
                    entered_weight=None, entered_unit=None, **ex) for ex in exercises]
    return str(get_duckdb().execute(
        "INSERT INTO sessions (date, phase, exercises, kind) VALUES (?, 'maintenance', ?, "
        "'training') RETURNING id", [D, structs]).fetchone()[0])


def test_restating_a_pre_0003_row_keeps_it_exactly_and_omitting_the_muscle_rederives_it():
    """A legacy guess (Bench Press as core) is neither confirmed nor silently
    rewritten by a restatement; leaving muscle_group out re-derives it."""
    sid = _legacy_session({"name": "Bench Press", "muscle_group": "core"})
    d = _detail(sid)
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                               "exercises": [_as_input(x) for x in d["exercises"]]})
    ex = _detail(sid)["exercises"][0]
    assert {k: ex[k] for k in _PROVENANCE} == {k: d["exercises"][0][k] for k in _PROVENANCE}
    assert (ex["muscle_group"], ex["muscle_source"], ex["raw_name"], ex["load_type"]) == \
        ("core", None, None, None)
    fixed = {k: v for k, v in _as_input(d["exercises"][0]).items() if k != "muscle_group"}
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": [fixed]})
    ex = _detail(sid)["exercises"][0]
    assert (ex["muscle_group"], ex["muscle_source"]) == ("chest", "catalog")


def test_restating_a_pre_0003_unknown_keeps_its_muscle_and_confirm_records_it():
    sid = _legacy_session({"name": "Tate Pres", "muscle_group": "triceps"})
    d = _detail(sid)
    out = DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                                     "exercises": [_as_input(x) for x in d["exercises"]]})
    ex = _detail(sid)["exercises"][0]
    assert (ex["muscle_group"], ex["muscle_source"], ex["needs_review"]) == ("triceps", None, True)
    assert "older entry" in out["anomaly_flags"][0]["detail"]
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": [
        dict(_as_input(d["exercises"][0]), confirm_muscle=True)]})
    ex = _detail(sid)["exercises"][0]
    assert (ex["muscle_group"], ex["muscle_source"], ex["needs_review"]) == ("triceps", "caller", True)


def test_confirm_muscle_needs_a_muscle():
    sid = _log_typed()
    with pytest.raises(ValidationError):
        DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "exercises": [
            {"new": True, "name": "Tate Pres", "sets": 1, "confirm_muscle": True}]})


def test_duplicate_identities_each_keep_their_own_provenance():
    conf = log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="Incline DB Press", sets=1, reps=[10], weight=[70], unit="lb"),
        ExerciseModel(name="incline dumbbell press", sets=1, reps=[8], weight=[75], unit="lb"),
        ExerciseModel(name="Zercher squat variation", sets=1, reps=[5]),
        ExerciseModel(name="Zercher squat variation", muscle_group="quads", sets=1, reps=[5]),
    ]))
    sid = str(conf.session_id)
    d = _detail(sid)
    exs = [_as_input(x) for x in d["exercises"]]
    for order in (exs, [exs[1], exs[0], exs[3], exs[2]]):
        DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": order})
        after = sorted(_detail(sid)["exercises"], key=lambda x: (x["raw_name"], x["muscle_source"]))
        want = sorted(d["exercises"], key=lambda x: (x["raw_name"], x["muscle_source"]))
        for a, b in zip(after, want, strict=True):
            assert {k: a[k] for k in _PROVENANCE} == {k: b[k] for k in _PROVENANCE}


def test_a_renamed_exercise_does_not_inherit_the_old_muscle_as_an_override():
    sid = _log_typed()
    d = _detail(sid)
    exs = [_as_input(x) for x in d["exercises"]]
    exs[1]["name"] = "Cable Row"          # copied muscle_group "chest" from the bench
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": exs})
    row = _detail(sid)["exercises"][1]
    assert (row["name"], row["muscle_group"], row["muscle_source"]) == ("Cable Row", "lats", "catalog")
    exs[1]["confirm_muscle"] = True       # unless the user really means it
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": exs})
    row = _detail(sid)["exercises"][1]
    assert (row["muscle_group"], row["muscle_source"]) == ("chest", "caller")


def _codes(out: dict) -> list[str]:
    return [f["code"] for f in out["anomaly_flags"]]


def test_a_new_exercise_in_a_removed_ones_place_keeps_the_users_own_muscle():
    conf = log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="Pendlay Row", sets=1, reps=[5]),
        ExerciseModel(name="Tate Pres", muscle_group="triceps", sets=2, reps=[12, 12]),
    ]))
    sid = str(conf.session_id)
    d = _detail(sid)
    for new_name in ("JM Press", "Tate Press"):          # a new exercise, a typo fix
        exs = [_as_input(d["exercises"][0]),
               {"new": True, "name": new_name, "sets": 2, "reps": [8, 8], "muscle_group": "triceps"}]
        out = DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": exs})
        ex = _detail(sid)["exercises"][1]
        assert (ex["name"], ex["muscle_group"], ex["muscle_source"]) == (new_name, "triceps", "caller")
        assert "amend_not_applied" not in _codes(out)
        DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                                   "exercises": [_as_input(x) for x in d["exercises"]]})


def test_a_rename_does_not_carry_a_caller_set_muscle_unless_restated_explicitly():
    """P23: a muscle the caller/user had set (triceps) survived a rename to an
    unrelated back exercise just because the amend was built from the detail
    read-back (which carries the stored muscle_group)."""
    conf = log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="Barbell Bench Press", muscle_group="triceps", sets=1, reps=[5])]))
    sid = str(conf.session_id)
    d = _detail(sid)
    assert (d["exercises"][0]["muscle_group"], d["exercises"][0]["muscle_source"]) == \
        ("triceps", "caller")
    copied = dict(_as_input(d["exercises"][0]), name="Lat Pulldown")   # muscle_group: triceps
    out = DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": [copied]})
    row = _detail(sid)["exercises"][0]
    assert (row["name"], row["muscle_group"], row["muscle_source"]) == ("Lat Pulldown", "lats", "catalog")
    details = [f["detail"] for f in out["anomaly_flags"] if f["code"] == "amend_not_applied"]
    assert any("muscle_group triceps" in x and "Barbell Bench Press" in x
               and "confirm_muscle" in x for x in details)
    # an explicit confirmation keeps it ...
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": [
        dict(copied, name="Lat Pulldown", confirm_muscle=True, muscle_group="lats")]})
    # ... as does naming a muscle that differs from the old exercise's
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": [
        {"index": 0, "name": "Cable Row", "sets": 1, "reps": [5], "muscle_group": "biceps"}]})
    row = _detail(sid)["exercises"][0]
    assert (row["name"], row["muscle_group"], row["muscle_source"]) == ("Cable Row", "biceps", "caller")


def test_a_rename_with_confirm_muscle_keeps_a_caller_set_muscle():
    conf = log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="Barbell Bench Press", muscle_group="triceps", sets=1, reps=[5])]))
    sid = str(conf.session_id)
    d = _detail(sid)
    out = DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": [
        dict(_as_input(d["exercises"][0]), name="Lat Pulldown", confirm_muscle=True)]})
    row = _detail(sid)["exercises"][0]
    assert (row["muscle_group"], row["muscle_source"]) == ("triceps", "caller")
    assert not [f for f in out["anomaly_flags"] if "muscle_group" in f["detail"]]


def test_a_dropped_copied_muscle_is_reported():
    sid = _log_typed()
    d = _detail(sid)
    exs = [_as_input(x) for x in d["exercises"]]
    exs[1]["name"] = "Cable Row"
    out = DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": exs})
    details = [f["detail"] for f in out["anomaly_flags"] if f["code"] == "amend_not_applied"]
    assert any("muscle_group chest" in x and "confirm_muscle" in x for x in details)


def test_a_rename_does_not_carry_the_old_load_type():
    conf = log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="Dumbbell Bench Press", sets=2, reps=[10, 10], weight=[50, 50], unit="lb"),
        ExerciseModel(name="Pull-Up", sets=2, reps=[8, 8]),
    ]))
    sid = str(conf.session_id)
    d = _detail(sid)
    exs = [{k: v for k, v in _as_input(x).items() if k != "muscle_group"} for x in d["exercises"]]
    exs[0]["name"], exs[1]["name"] = "Barbell Bench Press", "Lat Pulldown"
    out = DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": exs})
    bench, pulldown = _detail(sid)["exercises"]
    assert bench["load_type"] == "total" and pulldown["load_type"] == "machine_stack"
    # an unchanged kg load keeps the user's pounds even across the rename
    assert (bench["entered_weight"], bench["entered_unit"]) == ([50.0, 50.0], "lb")
    notes = [f["detail"] for f in out["anomaly_flags"] if f["code"] == "amend_not_applied"]
    assert len(notes) == 2 and "load_type per_hand" in notes[0] and "default total" in notes[0]
    # a follow-up amend can still state it
    exs2 = [_as_input(x) for x in _detail(sid)["exercises"]]
    exs2[0]["load_type"] = "per_hand"
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": exs2})
    assert _detail(sid)["exercises"][0]["load_type"] == "per_hand"


def test_unknown_names_match_their_stored_entry_case_insensitively():
    sid = _log_typed()
    d = _detail(sid)
    exs = [_as_input(x) for x in d["exercises"]]
    exs[2]["name"] = "tate pres"
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": exs})
    ex = _detail(sid)["exercises"][2]
    assert (ex["raw_name"], ex["muscle_source"]) == ("tate pres", "unclassified")


def test_confirm_muscle_must_be_a_real_boolean():
    sid = _log_typed()
    with pytest.raises(ValidationError):
        DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "exercises": [
            {"new": True, "name": "Tate Pres", "sets": 1, "muscle_group": "triceps", "confirm_muscle": "yes"}]})


# --- explicit links (holistic r4: nothing inferred from names or positions) --------

def test_detail_numbers_exercises_by_stored_position():
    sid = _log_typed()
    assert [x["index"] for x in _detail(sid)["exercises"]] == [0, 1, 2]


@pytest.mark.parametrize("ex", [
    {"name": "Squat", "sets": 1},                                  # neither
    {"name": "Squat", "sets": 1, "index": 0, "new": True},         # both
    {"name": "Squat", "sets": 1, "index": -1},
    {"name": "Squat", "sets": 1, "index": "0"},
])
def test_every_amend_exercise_must_say_what_it_is(ex):
    sid = _log_typed()
    before_row = _row(sid)
    with pytest.raises(ValidationError):
        DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "exercises": [ex]})
    assert _row(sid) == before_row and _audit_rows() == []


@pytest.mark.parametrize("exs", [
    [{"index": 3, "name": "Squat", "sets": 1}],                    # out of range
    [{"index": 0, "name": "Squat", "sets": 1}, {"index": 0, "name": "Squat", "sets": 1}],
])
def test_bad_indexes_are_invalid_input_and_write_nothing(exs):
    sid = _log_typed()
    before_row = _row(sid)
    with pytest.raises((ValueError, ValidationError)) as e:
        DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "exercises": exs})
    assert error_payload(e.value)["error"] == "invalid_input"
    assert _row(sid) == before_row and _audit_rows() == []


def test_a_rename_is_caught_wherever_it_moves():
    """Remove, insert and reorder around the renamed exercise: its copied
    guess is still not applied, because the link is its index."""
    conf = log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="Barbell Curl", sets=1, reps=[10]),
        ExerciseModel(name="Zercher squat variation", sets=1, reps=[5]),
        ExerciseModel(name="Dumbbell Bench Press", sets=1, reps=[10], weight_kg=[20.0]),
    ]))
    sid = str(conf.session_id)
    d = _detail(sid)
    zercher, bench = (_as_input(x) for x in d["exercises"][1:])
    zercher["name"], bench["name"] = "Romanian Deadlift", "Barbell Bench Press"
    out = DISPATCH["session_amend"]({"session_id": sid, "date": d["date"], "exercises": [
        {"new": True, "name": "Hammer Curl", "sets": 1, "reps": [10]}, bench, zercher]})
    _, b, z = _detail(sid)["exercises"]
    assert (z["name"], z["muscle_source"]) == ("Romanian Deadlift", "catalog")
    assert z["muscle_group"] != "quads"
    assert (b["muscle_source"], b["load_type"]) == ("catalog", "total")
    # chest copied onto Barbell Bench is what it derives anyway: dropped silently
    notes = [f["detail"] for f in out["anomaly_flags"] if f["code"] == "amend_not_applied"]
    assert len(notes) == 2 and "quads" in notes[1] and "per_hand" in notes[0]


def test_reordered_duplicates_with_equal_loads_keep_their_own_entries():
    conf = log_session(SessionInput(date=D, exercises=[
        ExerciseModel(name="Squat", muscle_group="glutes", sets=1, reps=[5], weight_kg=[100.0]),
        ExerciseModel(name="Squat", sets=1, reps=[3], weight_kg=[100.0]),
        ExerciseModel(name="Lat Pulldown", sets=1, reps=[10], weight=[50], unit="lb"),
        ExerciseModel(name="lat pulldown", sets=1, reps=[6], weight_kg=[22.6796185]),
    ]))
    sid = str(conf.session_id)
    d = _detail(sid)
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                               "exercises": [_as_input(x) for x in reversed(d["exercises"])]})
    after = {x["index"]: x for x in _detail(sid)["exercises"]}
    for b in d["exercises"]:
        a = next(x for x in after.values() if x["reps"] == b["reps"] and x["name"] == b["name"])
        assert {k: a[k] for k in _PROVENANCE} == {k: b[k] for k in _PROVENANCE}


def test_a_pre_0003_name_now_an_alias_keeps_what_was_typed_as_raw_name():
    """Back then an unknown name was stored as typed; the catalog has since
    learned it as an alias. Restating it must not lose the typed text."""
    sid = _legacy_session({"name": "Incline Dumbbell Row", "muscle_group": "chest"},
                          {"name": "Tate Pres", "muscle_group": "triceps"})
    d = _detail(sid)
    DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                               "exercises": [_as_input(x) for x in d["exercises"]]})
    row, tate = _detail(sid)["exercises"]
    assert (row["name"], row["raw_name"]) == ("Chest-Supported Dumbbell Row", "Incline Dumbbell Row")
    assert (tate["name"], tate["raw_name"]) == ("Tate Pres", None)   # still unknown: unchanged


def _session_with_nameless() -> str:
    """Some older rows carry exercise entries with no name (and NULL entries):
    named Bench Press at 0, a nameless struct at 1, a NULL entry at 2, a named
    Squat at 3."""
    sid = _legacy_session({"name": "Bench Press", "muscle_group": "chest"},
                          {"name": "Squat", "muscle_group": "quads"})
    bench, squat = get_duckdb().execute(
        "SELECT exercises FROM sessions WHERE id = ?", [sid]).fetchone()[0]
    get_duckdb().execute("DELETE FROM sessions")
    return str(get_duckdb().execute(
        "INSERT INTO sessions (date, phase, exercises, kind) VALUES (?, 'maintenance', ?, "
        "'training') RETURNING id",
        [D, [bench, dict(bench, name=None, sets=3), None, squat]]).fetchone()[0])


def test_detail_shows_every_stored_exercise_including_nameless_ones_with_their_index():
    """P22 (a): nameless entries were hidden from the detail, so the caller
    could neither see nor reference them."""
    d = _detail(_session_with_nameless())
    assert [x["index"] for x in d["exercises"]] == [0, 1, 2, 3]
    assert [x["name"] for x in d["exercises"]] == ["Bench Press", None, None, "Squat"]
    nameless = d["exercises"][1]
    assert nameless["needs_review"] is True and "no name" in nameless["review_detail"]
    assert nameless["sets"] == 3
    assert d["needs_review_count"] == 2


def test_listing_counts_nameless_entries_like_the_detail_does():
    sid = _session_with_nameless()
    assert [s for s in DISPATCH["sessions"]({}) if str(s["id"]) == sid][0]["needs_review"] == 2


def test_amend_lists_every_stored_exercise_it_removed_nameless_ones_included():
    """P22 (b): a whole-session amend dropped nameless entries without a word."""
    sid = _session_with_nameless()
    out = DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "exercises": [
        {"index": 0, "name": "Bench Press", "sets": 1}]})
    removed = out["removed_exercises"]
    assert [(r["index"], r["name"]) for r in removed] == [(1, None), (2, None), (3, "Squat")]
    assert removed[0]["sets"] == 3 and removed[2]["muscle_group"] == "quads"
    assert "removed 3 stored exercise" in out["message"]
    (audit_id, *_), = _audit_rows()
    before = json.loads(_audit_rows()[0][3])["before"]["exercises"]
    assert len(before) == 4                      # and they survive in the audit entry


def test_amend_that_keeps_everything_removes_and_reports_nothing():
    sid = _log()
    d = _detail(sid)
    out = DISPATCH["session_amend"]({"session_id": sid, "date": d["date"],
                                     "exercises": [_as_input(x) for x in d["exercises"]]})
    assert out["removed_exercises"] == [] and "removed" not in out["message"]


def test_a_nameless_entry_can_be_referenced_by_index_to_give_it_a_name():
    sid = _session_with_nameless()
    out = DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "exercises": [
        {"index": 1, "name": "Squat", "sets": 3}]})
    assert [(r["index"], r["name"]) for r in out["removed_exercises"]] == [
        (0, "Bench Press"), (2, None), (3, "Squat")]
    assert [x["name"] for x in _detail(sid)["exercises"]] == ["Squat"]


def test_an_index_outside_the_stored_session_is_invalid_input():
    sid = _session_with_nameless()
    with pytest.raises(ValueError, match="indexes coach_session_detail shows"):
        DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-04", "exercises": [
            {"index": 4, "name": "Squat", "sets": 1}]})


# --- restore is itself audited (P21) -------------------------------------------------

def _restore_rows():
    return get_duckdb().execute(
        "SELECT id, trigger_signal, payload FROM decision_log "
        "WHERE event_type = 'session_restore' ORDER BY created_at").fetchall()


def test_restoring_an_amend_writes_its_own_audit_entry_holding_the_overwritten_row():
    sid = _log()
    original = _row(sid)
    DISPATCH["session_amend"]({"session_id": sid, "date": "2026-10-05",
                               "exercises": [{"new": True, "name": "Squat", "sets": 1}]})
    amended = _row(sid)
    (amend_audit, *_), = _audit_rows()
    out = restore_snapshot(amend_audit)
    assert _row(sid) == original
    (restore_audit, signal, payload), = _restore_rows()
    assert out.action == "restored" and out.audit_id == restore_audit
    assert signal == f"session {sid} restored from audit {amend_audit}"
    snap = json.loads(payload)
    assert snap["session_id"] == sid and snap["restored_from"] == str(amend_audit)
    assert snap["before"]["exercises"][0]["name"] == "Squat"      # the state it overwrote
    assert snap["after"]["date"] == "2026-10-04"
    # ... so the restore is reversible from its own entry
    restore_snapshot(restore_audit)
    assert _row(sid) == amended


def test_restoring_a_delete_is_audited_and_reversible_by_deleting_again():
    sid = _log()
    original = _row(sid)
    DISPATCH["session_delete"]({"session_id": sid})
    (delete_audit, *_), = _audit_rows()
    restore_snapshot(delete_audit)
    assert _row(sid) == original
    (restore_audit, _, payload), = _restore_rows()
    assert json.loads(payload)["before"] is None                  # nothing was overwritten
    restore_snapshot(restore_audit)
    assert _row(sid) is None
    assert len(_restore_rows()) == 2                              # undoing is audited too


def test_a_failed_restore_writes_no_audit_entry():
    with pytest.raises(ValueError):
        restore_snapshot("00000000-0000-0000-0000-000000000000")
    assert _restore_rows() == []
