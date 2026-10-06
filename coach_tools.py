"""Coach agent tool dispatcher.

Invoked by the MCP server (mcp_server.py) or directly from the shell:
`python coach_tools.py <cmd> <json>`.
All output is JSON to stdout.

Error contract (both surfaces): a failure prints/returns
    {"error": <code>, "exception": <class name>, "detail": <message>}
and the CLI exits 1. Codes:
    invalid_input — fix the arguments (bad vocabulary, missing key, wrong type,
                    out-of-range number or date). ONLY genuine validation failures
                    map here: pydantic ValidationError and ValueError (incl. the
                    explicit argument checks, `InputError`)
    db            — storage failure: halt and report; don't retry blindly
    internal      — everything else, notably programming errors (AttributeError,
                    KeyError, IndexError, TypeError, ...) raised inside a skill:
                    the caller cannot fix those by changing the input

List-returning tools return an object ({"sessions": [...], "count": n}, ...): a bare empty
list reaches an MCP client as empty content, indistinguishable from a failed call.

bash 0.5s budget per call. Skills are pure deterministic code.
"""
from __future__ import annotations

import functools
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Single-sourced surface defaults (AGENTS.md's three-places rule): the MCP
# wrappers import these for their signature defaults — change them HERE only.
DEFAULT_TREND_WINDOW_DAYS = 28
DEFAULT_SESSIONS_LIMIT = 10
DEFAULT_BODYWEIGHT_WINDOW_DAYS = 28
DEFAULT_SEARCH_LIMIT = 20
DEFAULT_MEMORY_KIND = "observation"
DEFAULT_SESSION_KIND = "training"

# Upper bound for list-size arguments (sessions / memory_search `limit`):
# generous (the 10K-row perf guards list everything) yet far below DuckDB's
# 64-bit LIMIT, which a 10**30 used to overflow into a `db` error (P53).
MAX_LIMIT = 100_000


class InputError(ValueError):
    """An explicit argument check failed (missing key, wrong type, out of
    range, unknown key). A ValueError so it maps to `invalid_input`."""


# Caller mistakes: pydantic ValidationError subclasses ValueError, as do the
# repo's own validation errors and InputError. KeyError / AttributeError /
# IndexError / TypeError are NOT here: raised inside a skill they are bugs the
# caller cannot fix (P53) -- handlers check their arguments explicitly instead.
_INPUT_ERROR_EXCS = (ValueError,)


def _parse_date(value, key: str = "date", *, bounded: bool = True) -> date | None:
    """ISO date argument. `bounded` applies the input boundary's plausible-date
    window (models.dates, P38): a year-0001 date used to overflow inside the
    reader (`internal`)."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InputError(f"{key} must be an ISO date string (YYYY-MM-DD), got {type(value).__name__}")
    d = date.fromisoformat(value)
    if bounded:
        from models.dates import check_plausible_date
        try:
            check_plausible_date(d)
        except ValueError as e:
            raise InputError(f"{key}: {e}") from None
    return d


def _require(args: dict, key: str, typ: type | None = None):
    """A required argument: present and (optionally) of the given type --
    an explicit check, so a missing key is invalid_input, never a KeyError."""
    if key not in args or args[key] is None:
        raise InputError(f"missing required argument '{key}'")
    v = args[key]
    if typ is not None and not isinstance(v, typ):
        raise InputError(f"{key} must be a {typ.__name__}, got {type(v).__name__}")
    return v


def _str_list(args: dict, key: str) -> list[str] | None:
    v = args.get(key)
    if v is None:
        return None
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise InputError(f"{key} must be a list of strings")
    return v


def _check_keys(data: dict, allowed, where: str = "argument", ignore=()) -> None:
    """Reject keys the tool does not accept (P50): an unknown key was silently
    dropped by the models and ignored by the readers, so `{"window": 7}` ran
    with the default window and a typo in a profile field lost the value."""
    unknown = sorted(str(k) for k in data if k not in allowed and k not in ignore)
    if unknown:
        raise InputError(f"unknown {where} key(s): {', '.join(repr(k) for k in unknown)}; "
                         f"allowed: {', '.join(sorted(allowed)) or '(none)'}")


def _accepts(*keys, model: str | None = None):
    """Decorator for a handler: its argument keys must be among `keys`, or the
    input fields of the named pydantic model (`model="SessionInput"`, resolved
    lazily so the CLI cold start stays cheap). THE single arg-key check shared
    by every handler (P50)."""
    def deco(fn):
        @functools.wraps(fn)
        def run(args: dict):
            allowed = set(keys)
            if model is not None:
                import importlib
                mod, _, cls = model.rpartition(".")
                allowed |= set(getattr(importlib.import_module(mod or "models"), cls).model_fields)
            _check_keys(args, allowed)
            return fn(args)
        return run
    return deco


def _int_arg(args: dict, key: str, default: int, *,
             minimum: int | None = None, maximum: int | None = None) -> int:
    """Strict integer argument: rejects "28" (string), 3.7 (float), bools —
    same acceptance rules as the typed MCP schemas (adversarial F5/F6/F7)."""
    v = args.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{key} must be an integer, got {type(v).__name__}")
    if minimum is not None and v < minimum:
        raise ValueError(f"{key} must be >= {minimum}")
    if maximum is not None and v > maximum:
        raise ValueError(f"{key} must be <= {maximum}")
    return v


def error_payload(e: Exception) -> dict:
    """Translate any exception into the documented three-code error contract."""
    from duckdb import Error as DuckDBError
    if isinstance(e, _INPUT_ERROR_EXCS):  # pydantic ValidationError subclasses ValueError
        code = "invalid_input"
    elif isinstance(e, DuckDBError):
        code = "db"
    else:
        code = "internal"
    # an echoed input may hold a lone surrogate, which no client can encode: escape it
    detail = str(e).encode("utf-8", "backslashreplace").decode("utf-8")
    return {"error": code, "exception": type(e).__name__, "detail": detail}


@_accepts(model="SessionInput")
def cmd_log_session(args: dict):
    from models import ExerciseModel, SessionInput
    from skills.session_logger import log_session
    exercises = args.get("exercises")
    if isinstance(exercises, list):  # a typo'd per-exercise key (weigth) is not dropped (P50)
        for i, ex in enumerate(exercises):
            if isinstance(ex, dict):
                _check_keys(ex, set(ExerciseModel.model_fields), f"exercises[{i}]")
    inp = SessionInput.model_validate(args)
    return log_session(inp).model_dump(mode="json")


@_accepts("exercise")
def cmd_safety_check(args: dict):
    from skills.safety_gate import check_exercise_safety
    return check_exercise_safety(_require(args, "exercise", str)).model_dump(mode="json")


@_accepts("date")
def cmd_recovery(args: dict):
    from skills.recovery import compute_recovery_score
    from models.dates import today
    d = _parse_date(args.get("date")) or today()
    return compute_recovery_score(d).model_dump(mode="json")


@_accepts("muscle", "window_days", "end_date")
def cmd_trend(args: dict):
    from models import MuscleGroup
    from skills.trend_analysis import get_specialization_trend
    muscle = MuscleGroup(_require(args, "muscle"))
    return get_specialization_trend(
        muscle,
        window_days=_int_arg(args, "window_days", DEFAULT_TREND_WINDOW_DAYS,
                             minimum=1, maximum=3650),
        end_date=_parse_date(args.get("end_date"), "end_date"),
    ).model_dump(mode="json")


@_accepts()
def cmd_snapshot(args: dict):
    from skills.snapshot import generate_phase_snapshot
    return generate_phase_snapshot().model_dump(mode="json")


@_accepts()
def cmd_intake_status(args: dict):
    from skills.intake import assess_intake
    return assess_intake().model_dump(mode="json")


@_accepts("limit")
def cmd_sessions(args: dict):
    from skills.sessions import list_sessions
    limit = _int_arg(args, "limit", DEFAULT_SESSIONS_LIMIT, minimum=1, maximum=MAX_LIMIT)
    rows = list_sessions(limit)
    return {"sessions": rows, "count": len(rows)}


@_accepts("session_id", "date")
def cmd_session_detail(args: dict):
    from skills.sessions import get_session_detail
    session_id, on_date = args.get("session_id"), args.get("date")
    if session_id is not None and not isinstance(session_id, str):
        raise ValueError("session_id must be a string UUID")
    details = get_session_detail(session_id=session_id, on_date=_parse_date(on_date, bounded=False))
    return {"sessions": [d.model_dump(mode="json") for d in details]}


@_accepts(model="SessionAmendInput")
def cmd_session_amend(args: dict):
    from skills.sessions import amend_session, parse_amend_input
    data = parse_amend_input(args)  # rejected before any write
    return amend_session(data).model_dump(mode="json")


@_accepts("session_id")
def cmd_session_delete(args: dict):
    from skills.sessions import delete_session
    return delete_session(_require(args, "session_id")).model_dump(mode="json")


@_accepts(model="models.bodyweight.BodyweightInput")
def cmd_bodyweight_log(args: dict):
    from models.bodyweight import BodyweightInput
    from skills.bodyweight import log_bodyweight
    inp = BodyweightInput.model_validate(args)  # rejected before any write
    return log_bodyweight(inp).model_dump(mode="json")


@_accepts("id", "date", "condition", "weight", "unit", "weight_kg", "scale", "notes", "clear")
def cmd_bodyweight_amend(args: dict):
    from skills.bodyweight import amend_bodyweight
    return amend_bodyweight(args).model_dump(mode="json")  # validated before any write


@_accepts("id")
def cmd_bodyweight_delete(args: dict):
    from skills.bodyweight import delete_bodyweight
    return delete_bodyweight(_require(args, "id")).model_dump(mode="json")


@_accepts("window_days", "end_date")
def cmd_bodyweight_history(args: dict):
    from skills.bodyweight import bodyweight_history
    return bodyweight_history(
        _int_arg(args, "window_days", DEFAULT_BODYWEIGHT_WINDOW_DAYS, minimum=1, maximum=3650),
        _parse_date(args.get("end_date"), "end_date") or date.today(),
    ).model_dump(mode="json")


@_accepts()
def cmd_injuries_list(args: dict):
    from skills.injuries import list_injuries
    injuries = [i.model_dump(mode="json") for i in list_injuries()]
    return {"injuries": injuries, "count": len(injuries)}


@_accepts("location", "status", "severity", "contraindicated_exercises", "safe_alternatives")
def cmd_injuries_seed(args: dict):
    from skills.injuries import seed_injury
    res = seed_injury(
        _require(args, "location"), _require(args, "status"), _require(args, "severity"),
        args.get("contraindicated_exercises"), args.get("safe_alternatives"),
    )
    message = f"seeded {res.injury.location.value}={res.injury.status.value}"
    if res.needs_review:
        message += (" — unmapped exercise names, confirm with the user: "
                    + ", ".join(res.needs_review))
    return {
        "ok": True,
        "message": message,
        "injury": res.injury.model_dump(mode="json"),
        "needs_review": res.needs_review,
    }


@_accepts()
def cmd_profile_get(args: dict):
    from skills.profile import get_profile
    p = get_profile()
    return p.model_dump(mode="json") if p else {"profile": None}


def _field_default(model, key: str):
    """What an explicit null clears a model field to: its default ([] for a list)."""
    return model.model_fields[key].get_default(call_default_factory=True)


@_accepts(model="UserProfile")
def cmd_profile_set(args: dict):
    from models import UserProfile
    from models import AvailabilityWindow, Goal
    from skills.profile import merge_profile
    # `updated_at` is what coach_profile_get returns but the server owns it:
    # accepted and ignored, so a get -> edit -> set round trip keeps working.
    args = {k: v for k, v in args.items() if k != "updated_at"}
    for key, model in (("goals", Goal), ("weekly_availability", AvailabilityWindow)):
        items = args.get(key)
        if isinstance(items, list):
            for i, item in enumerate(items):
                if isinstance(item, dict):
                    _check_keys(item, set(model.model_fields), f"{key}[{i}]")
    if not args:
        raise ValueError("profile is empty — provide at least one field "
                         "(goals, training_age, days_per_week, bodyweight_kg, ...)")
    # MERGE (P71): keys present overwrite, keys absent keep the stored value, an
    # explicit null clears that field (a list field to [], any other to None).
    cleaned = {k: _field_default(UserProfile, k) if v is None else v for k, v in args.items()}
    incoming = UserProfile.model_validate(cleaned)  # strict, before any write
    return merge_profile(incoming, set(args)).model_dump(mode="json")


@_accepts("text", "kind", "tags")
def cmd_memory_save(args: dict):
    from models import NoteKind
    from skills.memory import add_note
    note = add_note(
        _require(args, "text", str),
        kind=NoteKind(args.get("kind", DEFAULT_MEMORY_KIND)),
        tags=_str_list(args, "tags") or [],
    )
    return note.model_dump(mode="json")


@_accepts("query", "tags", "limit")
def cmd_memory_search(args: dict):
    from skills.memory import search_notes
    query = args.get("query")
    if query is not None and not isinstance(query, str):
        raise InputError(f"query must be a string, got {type(query).__name__}")
    notes = search_notes(
        query=query,
        tags=_str_list(args, "tags"),
        limit=_int_arg(args, "limit", DEFAULT_SEARCH_LIMIT, minimum=1, maximum=MAX_LIMIT),
    )
    return {"notes": [n.model_dump(mode="json") for n in notes], "count": len(notes)}


def _release_after_call(fn):
    """Run a handler inside skills.init.connection_scope(): the DuckDB file is
    open only for the duration of the call, so an idle MCP server (or any
    long-lived caller) never locks other processes out of the database."""
    @functools.wraps(fn)
    def run(args: dict):
        from skills.init import connection_scope, ensure_schema_current
        with connection_scope():
            ensure_schema_current()  # P75: no tool runs against a mismatched schema
            return fn(args)
    return run


# Both surfaces (CLI main() and mcp_server._run) dispatch through this table,
# so wrapping here covers every tool call on either surface.
_HANDLERS = {
    "log_session": cmd_log_session,
    "safety_check": cmd_safety_check,
    "recovery": cmd_recovery,
    "trend": cmd_trend,
    "snapshot": cmd_snapshot,
    "intake_status": cmd_intake_status,
    "sessions": cmd_sessions,
    "session_detail": cmd_session_detail,
    "session_amend": cmd_session_amend,
    "session_delete": cmd_session_delete,
    "bodyweight_log": cmd_bodyweight_log,
    "bodyweight_amend": cmd_bodyweight_amend,
    "bodyweight_delete": cmd_bodyweight_delete,
    "bodyweight_history": cmd_bodyweight_history,
    "injuries_list": cmd_injuries_list,
    "injuries_seed": cmd_injuries_seed,
    "profile_get": cmd_profile_get,
    "profile_set": cmd_profile_set,
    "memory_save": cmd_memory_save,
    "memory_search": cmd_memory_search,
}
DISPATCH = {name: _release_after_call(fn) for name, fn in _HANDLERS.items()}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(json.dumps({"available": list(DISPATCH)}))
        return
    cmd = sys.argv[1]
    fn = DISPATCH.get(cmd)
    if fn is None:
        # same three-code contract as every other failure (+ the list to pick from)
        print(json.dumps({"error": "invalid_input", "exception": "UnknownCommand",
                          "detail": f"unknown command '{cmd}'", "available": list(DISPATCH)}))
        sys.exit(1)
    try:
        # the argument is parsed INSIDE the error contract: malformed JSON, or
        # JSON that is not an object (handlers take a dict), is invalid_input —
        # never a traceback (P27)
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        if not isinstance(args, dict):
            raise InputError(f"arguments must be a JSON object, got {type(args).__name__}")
        result = fn(args)
        if hasattr(result, "model_dump_json"):
            print(result.model_dump_json(indent=2))
        else:
            print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        print(json.dumps(error_payload(e), indent=2, default=str))
        sys.exit(1)


if __name__ == "__main__":
    main()
