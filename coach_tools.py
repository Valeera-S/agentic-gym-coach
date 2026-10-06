"""Coach agent tool dispatcher.

Invoked by the MCP server (mcp_server.py) or directly from the shell:
`python coach_tools.py <cmd> <json>`.
All output is JSON to stdout.

Error contract (both surfaces): a failure prints/returns
    {"error": <code>, "exception": <class name>, "detail": <message>}
and the CLI exits 1. Codes:
    invalid_input — fix the arguments (bad vocabulary, missing key, wrong type)
    db            — storage failure: halt and report; don't retry blindly
    internal      — unexpected bug: halt and report

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

# Caller mistakes (bad value, missing key, wrong type) — the caller can fix
# these by re-issuing the call. Anything else is db/internal: halt and report.
_INPUT_ERROR_EXCS = (ValueError, TypeError, KeyError, AttributeError)


def _parse_date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


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
    return {"error": code, "exception": type(e).__name__, "detail": str(e)}


def cmd_log_session(args: dict):
    from models import SessionInput
    from skills.session_logger import log_session
    inp = SessionInput.model_validate(args)
    return log_session(inp).model_dump(mode="json")


def cmd_safety_check(args: dict):
    from skills.safety_gate import check_exercise_safety
    return check_exercise_safety(args["exercise"]).model_dump(mode="json")


def cmd_recovery(args: dict):
    from skills.recovery import compute_recovery_score
    d = _parse_date(args.get("date")) or date.today()
    return compute_recovery_score(d).model_dump(mode="json")


def cmd_trend(args: dict):
    from models import MuscleGroup
    from skills.trend_analysis import get_specialization_trend
    muscle = MuscleGroup(args["muscle"])
    return get_specialization_trend(
        muscle,
        window_days=_int_arg(args, "window_days", DEFAULT_TREND_WINDOW_DAYS,
                             minimum=1, maximum=3650),
        end_date=_parse_date(args.get("end_date")),
    ).model_dump(mode="json")


def cmd_snapshot(args: dict):
    from skills.snapshot import generate_phase_snapshot
    return generate_phase_snapshot().model_dump(mode="json")


def cmd_intake_status(args: dict):
    from skills.intake import assess_intake
    return assess_intake().model_dump(mode="json")


def cmd_sessions(args: dict):
    from skills.sessions import list_sessions
    limit = _int_arg(args, "limit", DEFAULT_SESSIONS_LIMIT, minimum=0)
    return list_sessions(limit)


def cmd_session_detail(args: dict):
    from skills.sessions import get_session_detail
    session_id, on_date = args.get("session_id"), args.get("date")
    if session_id is not None and not isinstance(session_id, str):
        raise ValueError("session_id must be a string UUID")
    details = get_session_detail(session_id=session_id, on_date=_parse_date(on_date))
    return {"sessions": [d.model_dump(mode="json") for d in details]}


def cmd_session_amend(args: dict):
    from models import SessionAmendInput
    from skills.sessions import amend_session
    data = SessionAmendInput.model_validate(args)  # rejected before any write
    return amend_session(data).model_dump(mode="json")


def cmd_session_delete(args: dict):
    from skills.sessions import delete_session
    return delete_session(args["session_id"]).model_dump(mode="json")


def cmd_bodyweight_log(args: dict):
    from models.bodyweight import BodyweightInput
    from skills.bodyweight import log_bodyweight
    inp = BodyweightInput.model_validate(args)  # rejected before any write
    return log_bodyweight(inp).model_dump(mode="json")


def cmd_bodyweight_history(args: dict):
    from skills.bodyweight import bodyweight_history
    return bodyweight_history(
        _int_arg(args, "window_days", DEFAULT_BODYWEIGHT_WINDOW_DAYS, minimum=1, maximum=3650),
        _parse_date(args.get("end_date")) or date.today(),
    ).model_dump(mode="json")


def cmd_injuries_list(args: dict):
    from skills.injuries import list_injuries
    return [i.model_dump(mode="json") for i in list_injuries()]


def cmd_injuries_seed(args: dict):
    from skills.injuries import seed_injury
    res = seed_injury(
        args["location"], args["status"], args["severity"],
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


def cmd_profile_get(args: dict):
    from skills.profile import get_profile
    p = get_profile()
    return p.model_dump(mode="json") if p else {"profile": None}


def cmd_profile_set(args: dict):
    from models import UserProfile
    from skills.profile import set_profile
    profile = UserProfile.model_validate(args)
    if profile == UserProfile():
        # An all-default profile would silently disarm the intake's empty-profile
        # signal (adversarial F3): refuse instead of writing it.
        raise ValueError("profile is empty — provide at least one field "
                         "(goals, training_age, days_per_week, bodyweight_kg, ...)")
    return set_profile(profile).model_dump(mode="json")


def cmd_memory_save(args: dict):
    from models import NoteKind
    from skills.memory import add_note
    note = add_note(
        args["text"],
        kind=NoteKind(args.get("kind", DEFAULT_MEMORY_KIND)),
        tags=args.get("tags", []),
    )
    return note.model_dump(mode="json")


def cmd_memory_search(args: dict):
    from skills.memory import search_notes
    notes = search_notes(
        query=args.get("query"),
        tags=args.get("tags"),
        limit=_int_arg(args, "limit", DEFAULT_SEARCH_LIMIT, minimum=0),
    )
    return [n.model_dump(mode="json") for n in notes]


def _release_after_call(fn):
    """Run a handler inside skills.init.connection_scope(): the DuckDB file is
    open only for the duration of the call, so an idle MCP server (or any
    long-lived caller) never locks other processes out of the database."""
    @functools.wraps(fn)
    def run(args: dict):
        from skills.init import connection_scope
        with connection_scope():
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
        print(json.dumps({"error": f"unknown command '{cmd}'", "available": list(DISPATCH)}))
        sys.exit(2)
    try:
        # the argument is parsed INSIDE the error contract: malformed JSON, or
        # JSON that is not an object (handlers take a dict), is invalid_input —
        # never a traceback (P27)
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        if not isinstance(args, dict):
            raise TypeError(f"arguments must be a JSON object, got {type(args).__name__}")
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
