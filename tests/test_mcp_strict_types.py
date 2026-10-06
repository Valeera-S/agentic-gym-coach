"""MCP and CLI agree on integer / number arguments (P68).

The MCP framework coerces arguments per the wrapper's type hints BEFORE the
handler's own checks run, so `window_days: true` ran with 1 day and "28" / 28.0
were accepted over MCP while the CLI rejected them. Wrappers now annotate int
and number parameters with strict types; a type error surfaces as a framework
tool error (is_error), not the {"error": ...} dict.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import mcp_server

BAD_INTS = [True, "28", 28.0, 2.5]


class _Err:
    """A framework-level tool error: raised as ToolError or returned is_error."""
    is_error = True

    def __init__(self, text: str):
        self.content = [type("T", (), {"text": text})()]


def _call(name: str, args: dict):
    from mcp.server.mcpserver.exceptions import ToolError
    try:
        return asyncio.run(mcp_server.mcp.call_tool(name, args))
    except ToolError as e:
        return _Err(str(e))


@pytest.mark.parametrize("bad", BAD_INTS)
@pytest.mark.parametrize("name, args, key", [
    ("coach_trend", {"muscle": "quads"}, "window_days"),
    ("coach_bodyweight_history", {}, "window_days"),
    ("coach_sessions", {}, "limit"),
    ("coach_memory_search", {}, "limit"),
    ("coach_log_session", {"date": "2026-10-01", "exercises": [{"name": "Squat", "sets": 1}]},
     "pre_recovery_score"),
    ("coach_session_amend", {"session_id": "00000000-0000-0000-0000-000000000001",
                             "date": "2026-10-01",
                             "exercises": [{"new": True, "name": "Squat", "sets": 1}]},
     "pre_recovery_score"),
    ("coach_injuries_seed", {"location": "left_knee", "status": "active"}, "severity"),
])
def test_a_non_integer_is_rejected_by_the_mcp_layer_like_the_cli(name, args, key, bad):
    r = _call(name, {**args, key: bad})
    assert r.is_error is True


def test_the_framework_error_is_not_the_error_dict():
    r = _call("coach_trend", {"muscle": "quads", "window_days": True})
    assert r.is_error is True
    assert not r.content[0].text.lstrip().startswith('{\n  "error"')


@pytest.mark.parametrize("bad", [True, "80", "80.5"])
@pytest.mark.parametrize("key", ["weight", "weight_kg"])
def test_bodyweight_numbers_are_strict_over_mcp_too(key, bad):
    r = _call("coach_bodyweight_log", {"date": "2026-10-01", "condition": "fed", key: bad,
                                       **({"unit": "kg"} if key == "weight" else {})})
    assert r.is_error is True


def test_real_integers_and_whole_floats_for_decimal_fields_still_work():
    ok = _call("coach_sessions", {"limit": 5})
    assert ok.is_error is False
    assert json.loads(ok.content[0].text)["count"] == 0
    r = _call("coach_bodyweight_log", {"date": "2026-10-01", "condition": "fed", "weight_kg": 80})
    assert r.is_error is False
    assert json.loads(r.content[0].text)["weight_kg"] == 80.0
    assert _call("coach_trend", {"muscle": "quads", "window_days": 14}).is_error is False


def test_the_generated_json_schema_types_stay_accurate():
    tools = {t.name: t.input_schema for t in asyncio.run(mcp_server.mcp.list_tools())}

    def kind(tool: str, prop: str):
        s = tools[tool]["properties"][prop]
        opts = s.get("anyOf") or [s]
        return {o.get("type") for o in opts} - {"null"}

    assert kind("coach_trend", "window_days") == {"integer"}
    assert kind("coach_sessions", "limit") == {"integer"}
    assert kind("coach_memory_search", "limit") == {"integer"}
    assert kind("coach_log_session", "pre_recovery_score") == {"integer"}
    assert kind("coach_injuries_seed", "severity") == {"integer"}
    assert kind("coach_bodyweight_log", "weight_kg") == {"number"}
    assert kind("coach_bodyweight_log", "weight") == {"number"}
    assert kind("coach_bodyweight_history", "window_days") == {"integer"}


def test_the_prompt_says_type_errors_surface_as_framework_tool_errors():
    from pathlib import Path
    prompt = (Path(__file__).resolve().parent.parent / "docs" / "COACH_PROMPT.md").read_text(encoding="utf-8")
    assert "framework tool error" in prompt
