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


def test_keyerror_and_attributeerror_map_to_internal():
    # P53: raised inside a skill they are bugs the caller cannot fix
    assert error_payload(KeyError("exercise"))["error"] == "internal"
    assert error_payload(AttributeError("'int' has no strip"))["error"] == "internal"


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


def test_sessions_limit_must_be_positive():
    assert cmd_sessions({"limit": 1}) == {"sessions": [], "count": 0}
    for bad in (0, -1):  # P53: limits are positive integers
        with pytest.raises(ValueError):
            cmd_sessions({"limit": bad})


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
    ("[1, 2]", "InputError"),
    ('"just a string"', "InputError"),
    ("null", "InputError"),
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


# --- unknown command follows the error contract (P43) ---------------------------

def _cli(*argv):
    import subprocess
    import sys
    from pathlib import Path
    return subprocess.run([sys.executable, "coach_tools.py", *argv],
                          cwd=Path(__file__).resolve().parents[1],
                          capture_output=True, text=True, timeout=60)


def test_cli_unknown_command_uses_the_error_contract():
    import json
    out = _cli("bogus")
    assert out.returncode == 1
    payload = json.loads(out.stdout)
    assert payload["error"] == "invalid_input"
    assert payload["exception"] == "UnknownCommand"
    assert "bogus" in payload["detail"]
    assert payload["available"] == list(DISPATCH)


def test_cli_no_args_and_help_still_list_commands_with_exit_0():
    import json
    for argv in ((), ("--help",)):
        out = _cli(*argv)
        assert out.returncode == 0
        assert json.loads(out.stdout) == {"available": list(DISPATCH)}


# --- P53: read-tool bounds and the error mapping ------------------------------------

@pytest.mark.parametrize("cmd, args", [
    ("recovery", {"date": "0001-01-01"}),
    ("trend", {"muscle": "quads", "end_date": "0001-01-01"}),
    ("bodyweight_history", {"end_date": "0001-01-01"}),
    ("recovery", {"date": "1999-12-31"}),
    ("trend", {"muscle": "quads", "end_date": "2099-01-01"}),
    ("bodyweight_history", {"end_date": "2099-01-01"}),
    ("recovery", {"date": 20260101}),  # not a string
])
def test_read_tool_dates_outside_the_plausible_range_are_invalid_input(cmd, args):
    with pytest.raises(ValueError) as exc:
        DISPATCH[cmd](args)
    assert error_payload(exc.value)["error"] == "invalid_input"


@pytest.mark.parametrize("bad", [10**30, 0, -1, 100_001, 1e30, 2.5, True, "5", None])
@pytest.mark.parametrize("cmd", ["sessions", "memory_search"])
def test_limits_are_positive_bounded_integers(cmd, bad):
    with pytest.raises(ValueError) as exc:
        DISPATCH[cmd]({"limit": bad})
    assert error_payload(exc.value)["error"] == "invalid_input"


def test_limit_bounds_are_inclusive():
    assert DISPATCH["sessions"]({"limit": 1})["count"] == 0
    assert DISPATCH["sessions"]({"limit": 100_000})["count"] == 0


@pytest.mark.parametrize("exc", [AttributeError("'NoneType' has no x"), KeyError("k"),
                                 IndexError("out of range"), TypeError("unexpected"),
                                 ZeroDivisionError(), OverflowError("too big")])
def test_programming_errors_map_to_internal(exc):
    assert error_payload(exc)["error"] == "internal"


@pytest.mark.parametrize("exc", [ValueError("bad"), coach_tools.InputError("missing x")])
def test_validation_errors_map_to_invalid_input(exc):
    assert error_payload(exc)["error"] == "invalid_input"


def test_pydantic_validation_error_maps_to_invalid_input():
    with pytest.raises(ValidationError) as exc:
        DISPATCH["bodyweight_log"]({"date": "2026-10-01"})
    assert error_payload(exc.value)["error"] == "invalid_input"


def test_a_bug_inside_a_reader_is_internal_not_invalid_input(monkeypatch):
    import mcp_server
    import skills.sessions

    def boom(limit):
        raise AttributeError("'NoneType' object has no attribute 'strip'")
    monkeypatch.setattr(skills.sessions, "list_sessions", boom)
    out = mcp_server.coach_sessions(limit=3)
    assert out["error"] == "internal" and out["exception"] == "AttributeError"


@pytest.mark.parametrize("cmd, args", [
    ("safety_check", {}), ("session_delete", {}), ("trend", {}),
    ("injuries_seed", {}), ("memory_save", {}),
])
def test_a_missing_required_argument_is_invalid_input(cmd, args):
    with pytest.raises(ValueError) as exc:
        DISPATCH[cmd](args)
    assert error_payload(exc.value)["error"] == "invalid_input"


# --- P50: unknown fields are rejected, not silently dropped ---------------------------

def test_profile_set_rejects_an_unknown_field_and_names_it():
    with pytest.raises(ValueError, match="bodyweight") as exc:
        DISPATCH["profile_set"]({"days_per_week": 4, "bodyweight": 80})
    assert error_payload(exc.value)["error"] == "invalid_input"
    assert "bodyweight_kg" in str(exc.value)  # the allowed keys are listed
    from skills.profile import get_profile
    assert get_profile() is None  # nothing was written


def test_profile_set_rejects_unknown_keys_inside_goals_and_availability():
    with pytest.raises(ValueError, match="targets"):
        DISPATCH["profile_set"]({"goals": [{"kind": "hypertrophy", "targets": ["quads"]}]})
    with pytest.raises(ValueError, match="day"):
        DISPATCH["profile_set"]({"weekly_availability": [{"weekday": "mon", "day": "x"}]})


def test_profile_get_then_set_round_trip_keeps_working():
    """The read-back carries updated_at (and every null field): sending it back must work."""
    DISPATCH["profile_set"]({"days_per_week": 4, "goals": [{"kind": "hypertrophy"}]})
    got = DISPATCH["profile_get"]({})
    assert got["updated_at"]
    got["days_per_week"] = 5
    again = DISPATCH["profile_set"](got)
    assert again["days_per_week"] == 5
    assert again["updated_at"] != got["updated_at"]  # server-owned: the echoed value is ignored


@pytest.mark.parametrize("cmd, args", [
    ("trend", {"muscle": "quads", "window": 7}),
    ("recovery", {"day": "2026-10-01"}),
    ("sessions", {"count": 3}),
    ("session_detail", {"id": "x"}),
    ("session_delete", {"session_id": "x", "force": True}),
    ("session_amend", {"session_id": "x", "date": "2026-10-01", "exercises": [], "nope": 1}),
    ("safety_check", {"exercise": "Squat", "extra": 1}),
    ("bodyweight_history", {"window": 7}),
    ("bodyweight_log", {"date": "2026-10-01", "condition": "fed", "kg": 80}),
    ("injuries_seed", {"location": "left_elbow", "status": "active", "severity": 3, "x": 1}),
    ("memory_save", {"text": "a", "tag": "b"}),
    ("memory_search", {"q": "a"}),
    ("log_session", {"date": "2026-10-01", "exercises": [{"name": "Squat", "sets": 1}], "notes": "x"}),
    ("snapshot", {"x": 1}),
    ("intake_status", {"x": 1}),
    ("injuries_list", {"x": 1}),
    ("profile_get", {"x": 1}),
])
def test_every_handler_rejects_unknown_argument_keys(cmd, args):
    with pytest.raises(ValueError, match="unknown") as exc:
        DISPATCH[cmd](args)
    assert error_payload(exc.value)["error"] == "invalid_input"


def test_the_rejection_lists_the_allowed_keys():
    with pytest.raises(ValueError) as exc:
        DISPATCH["trend"]({"muscle": "quads", "window": 7})
    msg = str(exc.value)
    assert "'window'" in msg
    assert "window_days" in msg and "end_date" in msg and "muscle" in msg


def test_log_session_rejects_an_unknown_exercise_key():
    with pytest.raises(ValueError, match="weigth"):
        DISPATCH["log_session"]({"date": "2026-10-01", "exercises": [
            {"name": "Squat", "sets": 1, "weigth": [100]}]})
    assert DISPATCH["sessions"]({})["count"] == 0


def test_known_keys_still_work_through_the_cli_wrapper():
    assert DISPATCH["trend"]({"muscle": "quads", "window_days": 7})["window_days"] == 7
