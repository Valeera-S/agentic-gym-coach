"""coach_session_template / coach_session_labels surfaces (UX3)."""

from __future__ import annotations

import pytest

import coach_tools
import mcp_server
from coach_tools import DISPATCH


def _log(label):
    return DISPATCH["log_session"]({"date": "2026-10-02", "label": label, "exercises": [
        {"name": "Lat Pulldown", "sets": 2, "reps": [10, 10], "weight": [60, 60], "unit": "lb"}]})


def test_cli_template_and_labels():
    _log("Back day")
    t = DISPATCH["session_template"]({"label": "back day"})
    assert t["label"] == "Back day" and t["exercises"][0]["unit"] == "lb"
    labels = DISPATCH["session_labels"]({})
    assert [r["label"] for r in labels["labels"]] == ["Back day"] and labels["count"] == 1


@pytest.mark.parametrize("cmd, args", [
    ("session_template", {"labl": "x"}),
    ("session_template", {}),
    ("session_template", {"label": 5}),
    ("session_labels", {"x": 1}),
])
def test_unknown_key_and_bad_args_are_invalid_input(cmd, args):
    with pytest.raises(Exception) as e:
        DISPATCH[cmd](args)
    assert coach_tools.error_payload(e.value)["error"] == "invalid_input"


def test_mcp_wrappers_pass_label_through():
    out = mcp_server.coach_log_session(date="2026-10-02", label="Back day", exercises=[
        {"name": "Lat Pulldown", "sets": 1, "reps": [10]}])
    assert "error" not in out
    assert mcp_server.coach_session_template(label="Back day")["label"] == "Back day"
    assert mcp_server.coach_session_labels()["count"] == 1


def test_mcp_amend_wrapper_sets_keeps_and_clears_label():
    sid = mcp_server.coach_log_session(date="2026-10-02", exercises=[
        {"name": "Lat Pulldown", "sets": 1, "reps": [10]}])["session_id"]
    ex = [{"index": 0, "name": "Lat Pulldown", "sets": 1, "reps": [10]}]
    out = mcp_server.coach_session_amend(session_id=sid, date="2026-10-02", exercises=ex,
                                         label="Back day")
    assert out["label"] == "Back day"
    assert mcp_server.coach_session_amend(session_id=sid, date="2026-10-02",
                                          exercises=ex)["label"] == "Back day"
    out = mcp_server.coach_session_amend(session_id=sid, date="2026-10-02", exercises=ex,
                                         clear=["label"])
    assert out["label"] is None
