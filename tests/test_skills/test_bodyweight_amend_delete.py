"""P74: bodyweight readings can be corrected (coach_bodyweight_amend) and removed
(coach_bodyweight_delete), each writing the complete pre-change row to
decision_log.payload in the same transaction, like the session tools."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

import coach_tools
import mcp_server
from coach_tools import DISPATCH
from skills.init import get_duckdb

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture(autouse=True)
def _real_clock(monkeypatch):
    """conftest pins the plausibility clock to 2035; these tests need the real
    'no date after today' bound."""
    import models.dates
    monkeypatch.setattr(models.dates, "today", lambda: TODAY)


def _log(**kw) -> dict:
    args = {"date": TODAY.isoformat(), "condition": "fed", "weight": 72.45, "unit": "kg",
            "scale": "test scale", "notes": "after lunch"}
    args.update(kw)
    return DISPATCH["bodyweight_log"](args)


def _row(rid):
    return get_duckdb().execute(
        "SELECT date, weight_kg, weight_entered, weight_unit, condition, scale, notes, created_at "
        "FROM bodyweight_log WHERE id = ?", [rid]).fetchone()


def _audits(event):
    return get_duckdb().execute(
        "SELECT id, event_type, payload FROM decision_log WHERE event_type = ?", [event]).fetchall()


def _count():
    return get_duckdb().execute("SELECT count(*) FROM bodyweight_log").fetchone()[0]


# --- amend ----------------------------------------------------------------

def test_amend_date_keeps_everything_else_and_audits_the_full_old_row():
    r = _log(date=TODAY.isoformat())
    before = _row(r["id"])
    out = DISPATCH["bodyweight_amend"]({"id": r["id"], "date": YESTERDAY.isoformat()})
    assert out["changed"] is True and out["action"] == "amended"
    after = _row(r["id"])
    assert after[0] == YESTERDAY
    assert after[1:] == before[1:]  # kg, entered value + unit, condition, scale, notes, created_at
    assert out["reading"]["date"] == YESTERDAY.isoformat() and out["reading"]["id"] == r["id"]
    (aid, ev, payload), = _audits("bodyweight_amend")
    assert str(aid) == out["audit_id"]
    p = json.loads(payload)
    assert p["bodyweight_id"] == r["id"]
    assert p["before"]["date"] == TODAY.isoformat()
    assert p["before"]["weight_kg"] == 72.45 and p["before"]["condition"] == "fed"
    assert p["before"]["scale"] == "test scale" and p["before"]["notes"] == "after lunch"
    assert p["before"]["weight_entered"] == 72.45 and p["before"]["weight_unit"] == "kg"
    assert p["after"]["date"] == YESTERDAY.isoformat()


def test_amend_condition_and_value_in_pounds():
    r = _log()
    out = DISPATCH["bodyweight_amend"]({"id": r["id"], "condition": "morning_fasted",
                                        "weight": 144.0, "unit": "lb"})
    row = _row(r["id"])
    assert row[4] == "morning_fasted" and row[2] == 144.0 and row[3] == "lb"
    assert row[1] == pytest.approx(144.0 * 0.45359237)
    assert out["reading"]["weight_unit"] == "lb"


def test_amend_kg_only_replaces_the_entered_value_and_unit():
    r = _log(weight=150.0, unit="lb")
    DISPATCH["bodyweight_amend"]({"id": r["id"], "weight_kg": 66.0})
    row = _row(r["id"])
    assert row[1] == 66.0 and row[2] is None and row[3] is None


def test_amend_scale_and_notes_and_clear():
    r = _log()
    DISPATCH["bodyweight_amend"]({"id": r["id"], "notes": "corrected"})
    assert _row(r["id"])[5:7] == ("test scale", "corrected")
    out = DISPATCH["bodyweight_amend"]({"id": r["id"], "clear": ["scale", "notes"]})
    assert _row(r["id"])[5:7] == (None, None) and out["changed"] is True
    with pytest.raises(ValueError, match="clear"):
        DISPATCH["bodyweight_amend"]({"id": r["id"], "clear": ["weight_kg"]})


def test_amend_that_changes_nothing_writes_no_audit():
    r = _log()
    before = _row(r["id"])
    for args in ({}, {"date": TODAY.isoformat()}, {"weight": 72.45, "unit": "kg"},
                 {"condition": "fed", "scale": "test scale"}):
        out = DISPATCH["bodyweight_amend"]({"id": r["id"], **args})
        assert out["changed"] is False and out["audit_id"] is None, args
    assert _row(r["id"]) == before
    assert _audits("bodyweight_amend") == []


def test_amend_is_validated_like_a_fresh_log_and_writes_nothing_on_rejection():
    r = _log()
    before = _row(r["id"])
    bad = [
        {"date": (TODAY + timedelta(days=1)).isoformat()},    # in the future
        {"date": "0001-01-01"},                               # implausible
        {"date": "not-a-date"},
        {"condition": "after_nap"},                           # outside the vocabulary
        {"weight": 0, "unit": "kg"}, {"weight_kg": 401.0}, {"weight_kg": -3.0},
        {"weight": 70.0},                                     # unit missing
        {"unit": "lb"},                                       # value missing
        {"weight": 70.0, "unit": "stone"},
        {"weight": 70.0, "unit": "kg", "weight_kg": 60.0},    # the two disagree
        {"weight": True, "unit": "kg"}, {"weight_kg": "80"},  # strict types
        {"notes": "bad\x00text"}, {"scale": "bad\x07"},
        {"colour": "red"},                                    # unknown key
    ]
    for args in bad:
        with pytest.raises(ValueError):
            DISPATCH["bodyweight_amend"]({"id": r["id"], **args})
    assert _row(r["id"]) == before
    assert _audits("bodyweight_amend") == []


@pytest.mark.parametrize("bad_id", [None, "", "abc", 7, True, ["x"], "12345"])
def test_amend_and_delete_reject_malformed_ids_with_a_clear_message(bad_id):
    with pytest.raises(ValueError, match="id"):
        DISPATCH["bodyweight_amend"]({"id": bad_id, "notes": "x"})
    with pytest.raises(ValueError, match="id"):
        DISPATCH["bodyweight_delete"]({"id": bad_id})


def test_missing_id_is_invalid_input():
    with pytest.raises(ValueError, match="missing required argument 'id'"):
        DISPATCH["bodyweight_delete"]({})
    with pytest.raises(ValueError, match="missing required argument 'id'"):
        DISPATCH["bodyweight_amend"]({"notes": "x"})


def test_unknown_id_is_invalid_input_and_nothing_is_written():
    r = _log()
    for cmd, extra in (("bodyweight_amend", {"notes": "x"}), ("bodyweight_delete", {})):
        with pytest.raises(ValueError, match="no bodyweight reading with id"):
            DISPATCH[cmd]({"id": str(uuid4()), **extra})
    assert _count() == 1 and _row(r["id"]) is not None
    assert _audits("bodyweight_amend") == [] and _audits("bodyweight_delete") == []


def test_unknown_id_maps_to_the_invalid_input_error_code():
    try:
        DISPATCH["bodyweight_delete"]({"id": str(uuid4())})
    except Exception as e:
        assert coach_tools.error_payload(e)["error"] == "invalid_input"
    else:
        raise AssertionError("expected an error")


def test_amended_date_moves_the_reading_out_of_the_window():
    r = _log(condition="morning_fasted", date=TODAY.isoformat())
    far = (TODAY - timedelta(days=60)).isoformat()
    DISPATCH["bodyweight_amend"]({"id": r["id"], "date": far})
    hist = DISPATCH["bodyweight_history"]({"window_days": 28})
    assert hist["readings"] == [] and hist["window"]["averages"] == []


# --- delete ---------------------------------------------------------------

def test_delete_removes_the_row_and_audits_the_complete_row_first():
    keep = _log(weight=70.0)
    r = _log()
    snap = _row(r["id"])
    out = DISPATCH["bodyweight_delete"]({"id": r["id"]})
    assert out["action"] == "deleted" and out["id"] == r["id"]
    assert _row(r["id"]) is None and _row(keep["id"]) is not None and _count() == 1
    (aid, ev, payload), = _audits("bodyweight_delete")
    assert str(aid) == out["audit_id"]
    p = json.loads(payload)
    assert p["bodyweight_id"] == r["id"]
    b = p["before"]
    assert (b["date"], b["weight_kg"], b["weight_entered"], b["weight_unit"], b["condition"],
            b["scale"], b["notes"]) == (snap[0].isoformat(), snap[1], snap[2], snap[3], snap[4],
                                        snap[5], snap[6])
    assert b["id"] == r["id"] and b["created_at"]
    assert out["reading"]["weight_kg"] == 72.45  # what was deleted, echoed to the user


def test_delete_twice_is_invalid_input():
    r = _log()
    DISPATCH["bodyweight_delete"]({"id": r["id"]})
    with pytest.raises(ValueError, match="no bodyweight reading"):
        DISPATCH["bodyweight_delete"]({"id": r["id"]})


def test_the_change_and_its_audit_commit_together(monkeypatch):
    """If the audit insert fails, the change is rolled back."""
    import skills.bodyweight as bw
    r = _log()
    before = _row(r["id"])

    def boom(*a, **k):
        raise RuntimeError("audit failed")
    monkeypatch.setattr(bw, "_audit", boom)
    with pytest.raises(RuntimeError):
        DISPATCH["bodyweight_amend"]({"id": r["id"], "date": YESTERDAY.isoformat()})
    with pytest.raises(RuntimeError):
        DISPATCH["bodyweight_delete"]({"id": r["id"]})
    assert _row(r["id"]) == before


# --- surfaces -------------------------------------------------------------

def test_mcp_wrappers_exist_with_strict_types_and_round_trip():
    from mcp.server.mcpserver.exceptions import ToolError
    assert {"coach_bodyweight_amend", "coach_bodyweight_delete"} <= set(dir(mcp_server))
    r = _log()
    asyncio.run(mcp_server.mcp.call_tool(
        "coach_bodyweight_amend", {"id": r["id"], "date": YESTERDAY.isoformat()}))
    assert _row(r["id"])[0] == YESTERDAY
    for bad in (True, "80"):
        with pytest.raises(ToolError):
            asyncio.run(mcp_server.mcp.call_tool("coach_bodyweight_amend",
                                                 {"id": r["id"], "weight_kg": bad}))
    asyncio.run(mcp_server.mcp.call_tool("coach_bodyweight_delete", {"id": r["id"]}))
    assert _row(r["id"]) is None


def test_persona_requires_readback_and_an_explicit_yes():
    root = Path(__file__).resolve().parent.parent.parent
    text = (root / "docs" / "COACH_PROMPT.md").read_text(encoding="utf-8")
    i = text.index("coach_bodyweight_amend")
    para = text[i:i + 2500]
    assert "coach_bodyweight_delete" in text and "coach_bodyweight_history" in para
    assert "explicit yes" in para
