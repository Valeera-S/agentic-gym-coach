"""sessions — read back, amend and delete logged sessions.

The single owner of whole-session reads and of corrections (the other writers are
log_session in skills/session_logger.py and scripts/ingest_log.py --reset).

`coach_sessions` lists sessions leanly (Tier-1 friendly); this skill returns
ONE session — or every session on one date — exactly as logged: each exercise
with its per-set reps / rpe / weights, the canonical identity next to the raw
name the caller typed, how its muscle was decided (`muscle_source`), how its
weight was read (`load_type`) and the weight + unit as entered.

`needs_review` is derived from what is stored, never from a log-time message:
  - muscle_source keyword | unclassified          -> True (a guess / no muscle)
  - muscle_source caller on a name not in catalog -> True (unconfirmed name)
  - muscle_source catalog, or caller on a catalog name -> False
  - muscle_source NULL (a pre-0003 row): True iff the stored name is not a
    catalog identity today (those rows were guessed back then)

Unknown id or date -> ValueError (the tool contract's invalid_input).
Contract: deterministic. Reads are read-only; amend_session / delete_session write the
sessions row and its decision_log audit entry in one transaction. The restore_snapshot
recovery helper rewrites the sessions row in one transaction and writes no audit entry
of its own.
"""

from __future__ import annotations

import struct
from datetime import date
import json
from functools import lru_cache
from uuid import UUID

from models import (
    DecisionEventType,
    ExerciseDetail,
    MuscleGroup,
    MuscleSource,
    PhaseType,
    SessionAmendInput,
    SessionChange,
    SessionDetail,
    SessionInput,
)
from models.exercise_catalog import resolve_name

from .init import get_duckdb
from .session_logger import prepare_session, review_detail

_COLS = "id, date, phase, kind, pre_recovery_score, post_feedback, created_at, exercises"


def _f32(x: float | None) -> float | None:
    """The shortest decimal that is the same float32 as `x`.

    reps / rpe are stored as FLOAT (float32): 7.3 comes back as
    7.300000190734863. Read-back must return what was logged, so it returns the
    shortest decimal that rounds to the same float32 — the same normalization
    migration 0003 applied to legacy weights.
    """
    if x is None:
        return None
    target = struct.unpack("f", struct.pack("f", x))[0]
    for digits in range(1, 10):
        candidate = float(f"{x:.{digits}g}")
        if struct.unpack("f", struct.pack("f", candidate))[0] == target:
            return candidate
    return x


@lru_cache(maxsize=4096)
def _needs_review(name: str | None, source: str | None) -> bool:
    # memoized: a listing asks this for the same few (name, source) pairs
    # thousands of times, and the answer depends on nothing else
    if name is None:
        return True  # not producible by the logger; never assume it is confirmed
    known = resolve_name(name) is not None
    if source is None:
        return not known
    if source in (MuscleSource.keyword.value, MuscleSource.unclassified.value):
        return True
    if source == MuscleSource.caller.value:
        return not known
    if source == MuscleSource.catalog.value:
        return False
    return True  # a source this version does not know: never assume it is confirmed


def _exercise(e: dict) -> ExerciseDetail:
    source = e.get("muscle_source")
    flag = _needs_review(e["name"], source)
    detail = _review_text(e["name"], e.get("muscle_group"), source) if flag else None
    unit = e.get("entered_unit")
    return ExerciseDetail(
        name=e["name"],
        raw_name=e.get("raw_name"),
        muscle_group=e.get("muscle_group"),
        muscle_source=source,
        needs_review=flag,
        review_detail=detail,
        sets=e.get("sets"),
        reps=[_f32(r) for r in (e.get("reps") or [])],
        rpe=[_f32(r) for r in (e.get("rpe") or [])],
        weight_kg=list(e.get("weight_kg") or []),
        load_type=e.get("load_type"),
        load_type_unknown=e.get("load_type") is None,
        # what the user entered: the weight+unit form if they used it, else
        # the kg numbers they sent as weight_kg
        weight_as_entered=list(e["entered_weight"]) if unit else list(e.get("weight_kg") or []),
        unit_as_entered=unit or "kg",
        entered_weight=list(e["entered_weight"]) if e.get("entered_weight") is not None else None,
        entered_unit=unit,
        tempo=e.get("tempo"),
        form_quality=e.get("form_quality"),
        pain_flag=e.get("pain_flag"),
        notes=e.get("notes"),
    )


def _review_text(name: str, muscle: str | None, source: str | None) -> str:
    """The review reason, worded like the log-time flag. Never fails on a
    stored value an older or newer vocabulary would not know."""
    if source is None:
        return (f"exercise '{name}' is not in the catalog (logged before muscle "
                "sources were recorded); confirm its muscle with the user")
    try:
        return review_detail(name, MuscleGroup(muscle) if muscle else None, MuscleSource(source))
    except ValueError:
        return f"exercise '{name}' needs review (stored muscle {muscle}, source {source})"


# The listing's page order (newest first, deterministic tie-break).
_ORDER = "date DESC, created_at DESC, id"


def list_sessions(limit: int) -> list[dict]:
    """The lean coach_sessions listing, newest first, with an additive
    `needs_review` count per session.

    No exercise data and no dataframe library cross into Python (a CLI call
    must stay inside its 0.5 s budget, and Polars/pyarrow alone cost ~0.3 s to
    import). DuckDB groups the page's exercises by (name, muscle_source) with
    the page positions each pair occurs at; each distinct pair is decided once
    here (the catalog lookup, memoized) and its positions are counted. Cost is
    linear in the page's exercises; each distinct pair is decided only once.
    Exercises without a name (not producible by the logger) are skipped, as
    session detail skips them.
    """
    d = get_duckdb()
    rows = d.execute(
        "SELECT id, date, phase, pre_recovery_score, post_feedback, kind FROM sessions "
        f"ORDER BY {_ORDER} LIMIT ?", [limit]).fetchall()
    pairs = d.execute(
        f"""WITH page AS (
                SELECT id, row_number() OVER (ORDER BY {_ORDER}) - 1 AS pos
                FROM sessions ORDER BY {_ORDER} LIMIT ?)
            SELECT e.name, e.muscle_source, list(pos)
            FROM (SELECT p.pos, UNNEST(s.exercises) AS e
                  FROM page p JOIN sessions s USING (id))
            WHERE e IS NOT NULL AND e.name IS NOT NULL
            GROUP BY e.name, e.muscle_source""",
        [limit]).fetchall()
    counts = [0] * len(rows)
    for name, source, positions in pairs:
        if _needs_review(name, source):
            for pos in positions:
                counts[pos] += 1
    cols = ["id", "date", "phase", "pre_recovery_score", "post_feedback", "kind"]
    return [{**dict(zip(cols, r)), "needs_review": n} for r, n in zip(rows, counts)]


def _row_to_detail(row: tuple) -> SessionDetail:
    sid, d, phase, kind, score, feedback, created, exercises = row
    exs = [_exercise(e) for e in (exercises or []) if e is not None and e.get("name") is not None]
    return SessionDetail(
        id=sid, date=d, phase=phase, kind=kind, pre_recovery_score=score,
        post_feedback=feedback, created_at=created, exercises=exs,
        needs_review_count=sum(1 for e in exs if e.needs_review),
    )


def get_session_detail(session_id: str | UUID | None = None,
                       on_date: date | None = None) -> list[SessionDetail]:
    """Exactly one of `session_id` / `on_date`. Sessions on a date come back in
    logging order. Raises ValueError for both/neither or no match."""
    if (session_id is None) == (on_date is None):
        raise ValueError("give exactly one of session_id or date")
    if session_id is not None:
        sid = session_id if isinstance(session_id, UUID) else UUID(str(session_id))
        rows = get_duckdb().execute(
            f"SELECT {_COLS} FROM sessions WHERE id = ?", [sid]).fetchall()
        if not rows:
            raise ValueError(f"no session with id {sid}")
    else:
        rows = get_duckdb().execute(
            f"SELECT {_COLS} FROM sessions WHERE date = ? ORDER BY created_at, id",
            [on_date]).fetchall()
        if not rows:
            raise ValueError(f"no session logged on {on_date.isoformat()}")
    return [_row_to_detail(r) for r in rows]


# --- corrections: amend / delete -----------------------------------------------------
# Both write the COMPLETE pre-change row to decision_log.payload in the same
# transaction as the change, raw — exactly the stored values (float32 reps /
# rpe included), so the row can be put back from the audit entry alone
# (restore_snapshot()). An unknown id is invalid_input; nothing is written.

def _uuid(session_id) -> UUID:
    if isinstance(session_id, UUID):
        return session_id
    if not isinstance(session_id, str):
        raise ValueError("session_id must be a string UUID")
    return UUID(session_id)


def _snapshot(sid: UUID) -> dict | None:
    """The stored row, JSON-ready and complete (every column)."""
    row = get_duckdb().execute(f"SELECT {_COLS} FROM sessions WHERE id = ?", [sid]).fetchone()
    if row is None:
        return None
    sid_, d, phase, kind, score, feedback, created, exercises = row
    return {
        "id": str(sid_), "date": d.isoformat(), "phase": phase, "kind": kind,
        "pre_recovery_score": score, "post_feedback": feedback,
        "created_at": created.isoformat() if created is not None else None,
        "exercises": exercises,
    }


_PAST = {DecisionEventType.session_amend: "amended", DecisionEventType.session_delete: "deleted"}


def _audit(event: DecisionEventType, sid: UUID, before: dict, note: str,
           after: dict | None = None) -> UUID:
    payload = {"session_id": str(sid), "before": before}
    if after is not None:
        payload["after"] = after
    return get_duckdb().execute(
        """INSERT INTO decision_log (event_type, trigger_signal, reasoning_chain, payload)
           VALUES (?, ?, ?, ?) RETURNING id""",
        [event.value, f"session {sid} {_PAST[event]}", note,
         json.dumps(payload, ensure_ascii=False)],
    ).fetchone()[0]


def _in_transaction(work):
    d = get_duckdb()
    d.execute("BEGIN TRANSACTION")
    try:
        result = work()
    except BaseException:
        d.execute("ROLLBACK")
        raise
    d.execute("COMMIT")
    return result


def _amended(data: SessionAmendInput, name: str, before: dict):
    """New value: cleared if listed in `clear`, the given value, else kept."""
    if name in data.clear:
        return None
    value = getattr(data, name)
    return value if value is not None else before[name]


def amend_session(data: SessionAmendInput) -> SessionChange:
    """Replace a session's date and exercises, keeping its id (and created_at).

    The new content goes through prepare_session(), exactly like a fresh log.
    Fields left out or null (phase, kind, pre_recovery_score, post_feedback)
    keep their stored values; `clear` removes post_feedback /
    pre_recovery_score explicitly.
    """
    sid = data.session_id
    before = _snapshot(sid)
    if before is None:
        raise ValueError(f"no session with id {sid}")
    fresh = SessionInput(
        date=data.date, exercises=data.exercises,
        phase=data.phase or (PhaseType(before["phase"]) if before["phase"] else None),
        kind=data.kind or before["kind"],
        pre_recovery_score=_amended(data, "pre_recovery_score", before),
        post_feedback=_amended(data, "post_feedback", before),
    )
    phase, structs, flags = prepare_session(fresh)

    def work():
        get_duckdb().execute(
            """UPDATE sessions SET date = ?, phase = ?, kind = ?, pre_recovery_score = ?,
                                   post_feedback = ?, exercises = ?
               WHERE id = ?""",
            [fresh.date, phase.value, fresh.kind.value, fresh.pre_recovery_score,
             fresh.post_feedback, structs, sid])
        return _audit(DecisionEventType.session_amend, sid, before,
                      f"amended: {len(before['exercises'] or [])} -> {len(structs)} exercise(s)",
                      after=_snapshot(sid))

    audit_id = _in_transaction(work)
    return SessionChange(action="amended", session_id=sid, audit_id=audit_id,
                         anomaly_flags=flags, phase=phase.value, kind=fresh.kind.value,
                         pre_recovery_score=fresh.pre_recovery_score,
                         post_feedback=fresh.post_feedback,
                         message="session amended; previous version audited")


def delete_session(session_id) -> SessionChange:
    """Hard-delete one session; its complete row is audited first."""
    sid = _uuid(session_id)
    before = _snapshot(sid)
    if before is None:
        raise ValueError(f"no session with id {sid}")

    def work():
        audit_id = _audit(DecisionEventType.session_delete, sid, before,
                          f"deleted: {before['date']}, {len(before['exercises'] or [])} exercise(s)")
        get_duckdb().execute("DELETE FROM sessions WHERE id = ?", [sid])
        return audit_id

    audit_id = _in_transaction(work)
    return SessionChange(action="deleted", session_id=sid, audit_id=audit_id,
                         message="session deleted; its full row is in the audit trail")


def restore_snapshot(audit_id) -> UUID:
    """Put a session back exactly as an audit entry recorded it before the
    change (a deleted session is re-inserted; an amended one is overwritten).
    For recovery by the coding agent — deliberately not a coach tool."""
    row = get_duckdb().execute(
        "SELECT event_type, payload FROM decision_log WHERE id = ?", [_uuid(audit_id)]).fetchone()
    if row is None or row[1] is None:
        raise ValueError(f"no restorable audit entry {audit_id}")
    before = json.loads(row[1])["before"]
    sid = UUID(before["id"])

    def work():
        d = get_duckdb()
        d.execute("DELETE FROM sessions WHERE id = ?", [sid])
        d.execute(
            """INSERT INTO sessions (id, date, phase, kind, pre_recovery_score, post_feedback,
                                     created_at, exercises)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [sid, before["date"], before["phase"], before["kind"], before["pre_recovery_score"],
             before["post_feedback"], before["created_at"], before["exercises"]])
        return sid

    return _in_transaction(work)
