"""coach_tools dispatcher contract — error vocabulary (T8), strict int args
(F6/F7), boundary guards (F3, injuries T1), single-sourced defaults (M4)."""

import pytest
from duckdb import Error as DuckDBError
from pydantic import ValidationError

import coach_tools
from coach_tools import (
    DISPATCH,
    DEFAULT_TREND_WINDOW_DAYS,
    _int_arg,
    cmd_injuries_seed,
    cmd_profile_set,
    cmd_sessions,
    cmd_trend,
    error_payload,
)


# --- error vocabulary -------------------------------------------------------

def test_input_errors_map_to_invalid_input():
    p = error_payload(ValueError("bad muscle"))
    assert p == {"error": "invalid_input", "exception": "ValueError", "detail": "bad muscle"}


def test_keyerror_and_attributeerror_map_to_invalid_input():
    assert error_payload(KeyError("exercise"))["error"] == "invalid_input"
    assert error_payload(AttributeError("'int' has no strip"))["error"] == "invalid_input"


def test_db_errors_map_to_db_and_unknown_to_internal():
    assert error_payload(DuckDBError("lock"))["error"] == "db"
    assert error_payload(RuntimeError("boom"))["error"] == "internal"


# --- strict integer arguments (adversarial F5/F6/F7) ------------------------

def test_int_arg_rejects_strings_floats_bools():
    with pytest.raises(ValueError, match="integer"):
        _int_arg({"n": "28"}, "n", 10)
    with pytest.raises(ValueError, match="integer"):
        _int_arg({"n": 3.7}, "n", 10)
    with pytest.raises(ValueError, match="integer"):
        _int_arg({"n": True}, "n", 10)


def test_int_arg_enforces_bounds():
    with pytest.raises(ValueError, match=">= 1"):
        _int_arg({"n": -28}, "n", 28, minimum=1)
    with pytest.raises(ValueError, match="<= 3650"):
        _int_arg({"n": 4000}, "n", 28, maximum=3650)
    assert _int_arg({}, "n", 7) == 7  # default path untouched


def test_trend_window_mirrors_mcp_validation():
    with pytest.raises(ValueError):
        cmd_trend({"muscle": "quads", "window_days": "28"})
    with pytest.raises(ValueError):
        cmd_trend({"muscle": "quads", "window_days": -28})  # was a silent empty report


def test_trend_default_still_applies():
    out = cmd_trend({"muscle": "quads"})
    assert out["window_days"] == DEFAULT_TREND_WINDOW_DAYS
    assert out["effective_volume"] == 0.0


def test_sessions_limit_zero_ok_negative_rejected():
    assert cmd_sessions({"limit": 0}) == {"sessions": [], "count": 0}
    with pytest.raises(ValueError):
        cmd_sessions({"limit": -1})  # was a DuckDB BinderException shape


# --- boundary guards --------------------------------------------------------

def test_empty_profile_refused_and_not_written():
    with pytest.raises(ValueError, match="empty"):
        cmd_profile_set({})
    from skills.profile import get_profile
    assert get_profile() is None  # onboarding gate stays armed (F3)


def test_injuries_seed_via_dispatch_canonicalizes_and_gates():
    # The audit's done-when: messy spelling seeded through the real tool, then
    # the gate blocks the canonical query.
    out = cmd_injuries_seed({
        "location": "left_elbow", "status": "active", "severity": "4",
        "contraindicated_exercises": ["Dumbbell Skull Crusher"],
        "safe_alternatives": ["Tricep Pushdown"],
    })
    assert out["ok"] is True
    assert out["needs_review"] == []
    # dumbbell skull crushers are their own identity now (different implement)
    assert out["injury"]["contraindicated_exercises"] == ["Dumbbell Skull Crusher"]
    from skills.safety_gate import check_exercise_safety
    assert check_exercise_safety("dumbbell skull crusher").safe is False
    # the generic name may mean the banned variant: fail-closed
    assert check_exercise_safety("Skull Crusher").safe is False


def test_injuries_seed_via_dispatch_rejects_bad_vocab():
    with pytest.raises(ValidationError):
        DISPATCH["injuries_seed"]({"location": "elbow", "status": "active", "severity": 3})


# --- CLI argument parsing (P27) ------------------------------------------------

@pytest.mark.parametrize("raw, exc", [
    ("{bad json", "JSONDecodeError"),
    ("", "JSONDecodeError"),
    ("[1, 2]", "TypeError"),
    ('"just a string"', "TypeError"),
    ("null", "TypeError"),
])
def test_cli_rejects_malformed_or_non_object_json_with_the_error_contract(raw, exc):
    import json
    import subprocess
    import sys
    from pathlib import Path
    out = subprocess.run([sys.executable, "coach_tools.py", "sessions", raw],
                         cwd=Path(__file__).resolve().parents[1],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 1
    assert "Traceback" not in out.stderr
    payload = json.loads(out.stdout)
    assert payload["error"] == "invalid_input"
    assert payload["exception"] == exc
    assert payload["detail"]


# --- intake scan surface ------------------------------------------------------

def test_intake_status_reports_missing_and_ready_flags():
    out = DISPATCH["intake_status"]({})
    assert out["training_ready"] is False
    assert out["nutrition_ready"] is False
    assert "bodyweight_kg" in out["missing"]


# --- list-returning tools return an object (P42) -------------------------------

def test_list_tools_return_objects_even_when_empty():
    """A bare empty list reaches an MCP client as EMPTY content, which cannot be
    told from a failed call; every list-returning tool wraps its rows."""
    assert DISPATCH["sessions"]({}) == {"sessions": [], "count": 0}
    assert DISPATCH["injuries_list"]({}) == {"injuries": [], "count": 0}
    assert DISPATCH["memory_search"]({}) == {"notes": [], "count": 0}


def test_list_tools_count_matches_rows():
    DISPATCH["memory_save"]({"text": "a"})
    DISPATCH["memory_save"]({"text": "b"})
    out = DISPATCH["memory_search"]({})
    assert out["count"] == 2 == len(out["notes"])
    DISPATCH["log_session"]({"date": "2026-01-05", "exercises": [{"name": "Squat", "sets": 1}]})
    out = DISPATCH["sessions"]({})
    assert out["count"] == 1 == len(out["sessions"])


def test_no_argless_handler_returns_a_bare_list():
    """Every handler's result is a dict or a model: the MCP wrappers are typed
    `-> dict`, and an empty list would arrive as empty content."""
    for name in ("sessions", "injuries_list", "memory_search", "profile_get",
                 "intake_status", "snapshot", "bodyweight_history", "recovery"):
        assert not isinstance(DISPATCH[name]({}), list), name
