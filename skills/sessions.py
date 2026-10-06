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
recovery helper does the same and writes its own `session_restore` entry (holding the
state it overwrote), so a restore is itself reversible.
"""

from __future__ import annotations

import struct
from datetime import date
import json
from functools import lru_cache
from uuid import UUID

from models import (
    AnomalyCode,
    AnomalyFlag,
    DecisionEventType,
    ExerciseDetail,
    LoadType,
    MuscleGroup,
    MuscleSource,
    PhaseType,
    RemovedExercise,
    SessionAmendInput,
    SessionChange,
    SessionDetail,
    SessionInput,
    SessionKind,
    WeightUnit,
)
from models.exercise_catalog import classify, default_load_type, lookup_key, resolve_name
from models.session import _same_loads

from .init import get_duckdb
from .session_logger import prepare_session, review_detail

_COLS = ("id, date, phase, kind, pre_recovery_score, post_feedback, created_at, exercises, "
         "label")


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


@lru_cache(maxsize=1 << 16)
def _needs_review(name: str | None, source: str | None) -> bool:
    # memoized: the answer depends on nothing else. Sized above any realistic
    # page's distinct pairs: a smaller LRU is evicted in full by one cyclic
    # scan of a large page and then never hits
    if name is None:
        return True  # a nameless entry (older rows only); never assume it is confirmed
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


def _per_set(values: list | None, sets: int | None) -> list:
    """A stored per-set array as a list. NULL / empty means "unrecorded for
    every set" and reads as [None] * sets, exactly as trend and the snapshot
    pad it (P32); a non-empty array is returned as stored."""
    if values:
        return list(values)
    return [None] * sets if sets and sets > 0 else []


def _exercise(e: dict | None, index: int | None = None) -> ExerciseDetail:
    """One stored exercise as ExerciseDetail. A nameless entry (some older rows
    hold them, and NULL entries) is shown too — with its index, as needing
    review — so the caller can see and reference everything stored."""
    e = e or {}
    source = e.get("muscle_source")
    flag = _needs_review(e.get("name"), source)
    if e.get("name") is None:
        detail = (f"exercise entry {index} has no name (an older row); amend it by index "
                  "to name it, or leave its index out of the amend to remove it")
    else:
        detail = _review_text(e["name"], e.get("muscle_group"), source) if flag else None
    unit = e.get("entered_unit")
    sets = e.get("sets")
    weights = _per_set(e.get("weight_kg"), sets)
    as_entered = list(e["entered_weight"]) if unit else weights
    return ExerciseDetail(
        index=index,
        name=e.get("name"),
        raw_name=e.get("raw_name"),
        muscle_group=e.get("muscle_group"),
        muscle_source=source,
        needs_review=flag,
        review_detail=detail,
        sets=sets,
        reps=[_f32(r) for r in _per_set(e.get("reps"), sets)],
        rpe=[_f32(r) for r in _per_set(e.get("rpe"), sets)],
        weight_kg=weights,
        load_type=e.get("load_type"),
        load_type_unknown=e.get("load_type") is None,
        # what the user entered: the weight+unit form if they used it, else
        # the kg numbers they sent as weight_kg
        weight_as_entered=as_entered,
        # no weight entered (bodyweight / unrecorded sets) means no unit either (P61)
        unit_as_entered=(unit or "kg") if any(w is not None for w in as_entered) else None,
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
    import). One query returns the page's rows (with their rowid); a second
    groups the page's exercises by (name, muscle_source) with the rowids each
    pair occurs at. Each distinct pair is decided once (the catalog lookup,
    memoized) and its rowids are counted onto their page positions. The page
    is sorted only once: when it holds the whole table (the usual large-limit
    call) the second query needs no page restriction, and it skips
    catalog-sourced exercises (never flagged). `id` comes back as its string
    form: building 10,000 UUID objects alone cost ~13 ms, and every surface
    serializes it to a string anyway. Cost is linear in the page's exercises.
    A nameless entry (older rows only) counts as needing review, as in
    session detail.
    """
    d = get_duckdb()
    rows = d.execute(
        "SELECT rowid, CAST(id AS VARCHAR), date, phase, pre_recovery_score, post_feedback, kind, "
        "label "
        "FROM sessions "
        f"ORDER BY {_ORDER} LIMIT ?", [limit]).fetchall()
    if not rows:
        return []
    total = d.execute("SELECT count(*) FROM sessions").fetchone()[0]
    page = "" if len(rows) == total else (
        f"WHERE rowid IN (SELECT rowid FROM sessions ORDER BY {_ORDER} LIMIT ?)")
    pairs = d.execute(
        f"""SELECT e.name, e.muscle_source, list(rid)
            FROM (SELECT rowid AS rid, UNNEST(exercises) AS e FROM sessions {page})
            WHERE e.muscle_source IS DISTINCT FROM 'catalog' OR e.name IS NULL
            -- (a named catalog exercise is never flagged; a nameless entry always is)
            GROUP BY e.name, e.muscle_source""",
        [limit] if page else []).fetchall()
    position = {r[0]: i for i, r in enumerate(rows)}
    counts = [0] * len(rows)
    for name, source, rids in pairs:
        if _needs_review(name, source):
            for rid in rids:
                counts[position[rid]] += 1
    cols = ["id", "date", "phase", "pre_recovery_score", "post_feedback", "kind", "label"]
    return [{**dict(zip(cols, r[1:])), "needs_review": n} for r, n in zip(rows, counts)]


def _row_to_detail(row: tuple) -> SessionDetail:
    sid, d, phase, kind, score, feedback, created, exercises, label = row
    exs = [_exercise(e, i) for i, e in enumerate(exercises or [])]
    return SessionDetail(
        id=sid, date=d, phase=phase, kind=kind, pre_recovery_score=score,
        post_feedback=feedback, label=label, created_at=created, exercises=exs,
        needs_review_count=sum(1 for e in exs if e.needs_review),
    )


def get_session_detail(session_id: str | UUID | None = None,
                       on_date: date | None = None) -> list[SessionDetail]:
    """Exactly one of `session_id` / `on_date`. Sessions on a date come back in
    logging order. Raises ValueError for both/neither or no match."""
    if (session_id is None) == (on_date is None):
        raise ValueError("give exactly one of session_id or date")
    if session_id is not None:
        sid = _uuid(session_id)
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

def _uuid(value, what: str = "session_id") -> UUID:
    """A session (or audit) id as a UUID; anything else is invalid_input with a
    message that says what the id is and where it comes from (P63)."""
    if isinstance(value, UUID):
        return value
    try:
        if isinstance(value, str):
            return UUID(value)
    except ValueError:
        pass
    shown = repr(value) if len(repr(value)) <= 60 else repr(value)[:57] + "...'"
    raise ValueError(f"{what} must be a UUID (as returned by coach_sessions / "
                     f"coach_session_detail), got {shown}")


def _snapshot(sid: UUID) -> dict | None:
    """The stored row, JSON-ready and complete (every column)."""
    row = get_duckdb().execute(f"SELECT {_COLS} FROM sessions WHERE id = ?", [sid]).fetchone()
    if row is None:
        return None
    sid_, d, phase, kind, score, feedback, created, exercises, label = row
    return {
        "id": str(sid_), "date": d.isoformat(), "phase": phase, "kind": kind,
        "pre_recovery_score": score, "post_feedback": feedback, "label": label,
        "created_at": created.isoformat() if created is not None else None,
        "exercises": exercises,
    }


_PAST = {DecisionEventType.session_amend: "amended", DecisionEventType.session_delete: "deleted",
         DecisionEventType.session_restore: "restored"}


def _audit(event: DecisionEventType, sid: UUID, before: dict | None, note: str,
           after: dict | None = None, restored_from: UUID | None = None) -> UUID:
    """One audit entry. `before` is the row the change overwrites or removes
    (None for a restore that re-creates a deleted session: nothing existed)."""
    payload = {"session_id": str(sid), "before": before}
    if after is not None:
        payload["after"] = after
    signal = f"session {sid} {_PAST[event]}"
    if restored_from is not None:
        payload["restored_from"] = str(restored_from)
        signal += f" from audit {restored_from}"
    return get_duckdb().execute(
        """INSERT INTO decision_log (event_type, trigger_signal, reasoning_chain, payload)
           VALUES (?, ?, ?, ?) RETURNING id""",
        [event.value, signal, note,
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


def _identity(name: str | None) -> str:
    """Catalog identity, or for an unknown name its lookup key (case and
    whitespace insensitive, like the catalog's own lookups)."""
    return (resolve_name(name) or lookup_key(name)) if name else ""


def _referenced(exercises: list, stored: list[dict]) -> list[tuple[dict, bool] | None]:
    """Per amend exercise: (the stored exercise its `index` names, same
    identity?) or None for a `new` one. An index outside the stored session
    is invalid input. A nameless stored entry can be referenced too (coach_
    session_detail shows it); naming it is a rename from nothing."""
    out: list[tuple[dict, bool] | None] = []
    for ex in exercises:
        if ex.index is None:
            out.append(None)
            continue
        if ex.index >= len(stored):
            raise ValueError(f"index {ex.index} ('{ex.name}') is not an exercise of this "
                             "session; use the indexes coach_session_detail shows")
        s = stored[ex.index] or {}
        out.append((s, _identity(s.get("name")) == _identity(ex.name)))
    return out


def _provenance_to_keep(exercises: list, stored: list[dict] | None,
                        ) -> tuple[list[dict | None], list[AnomalyFlag]]:
    """What an amend keeps from the stored exercise each one references.

    An amend is usually built from coach_session_detail, whose exercises
    carry the stored canonical name, muscle, load_type and weight_kg.
    Re-validated like a fresh log, those would read as "the user typed the
    canonical name", "caller set this muscle", "the identity's default
    load" and "the user entered kg" — rewriting provenance nobody touched.
    Each amend exercise names its stored exercise by `index` (or is `new`:
    a fresh log, nothing kept). Of a referenced exercise of the SAME
    identity it keeps:
      - raw_name (also NULL = unknown), when `name` is exactly the stored name
        (a pre-0003 stored name the catalog now maps to another identity
        was typed as is, and becomes the raw_name);
      - muscle_source (also NULL), when `muscle_group` equals the stored
        muscle and `confirm_muscle` is not set — so restating never turns a
        guess into a caller-confirmed muscle and never re-decides a stored
        muscle. OMITTING `muscle_group` re-derives it like a fresh log (the
        way to replace an old guess); `confirm_muscle` records it as the
        caller's;
      - load_type, when none is given (NULL stays unknown);
      - weight + unit as entered, when only `weight_kg` is given and it
        equals the stored kg loads of a weight + unit entry.
    A referenced exercise of a DIFFERENT identity is a rename: it keeps only
    the weight + unit as entered (same rule), and values copied from the old
    exercise are not applied to the new name — a `muscle_group` equal to its
    stored muscle (unless `confirm_muscle`; a caller-set muscle is no
    exception — it belonged to the old exercise, and an unrelated new one
    must not inherit it), and a `load_type` equal to its stored one where the
    new identity's default differs (that default applies). A muscle_group
    that differs from the stored one is the caller's own choice and is kept.
    Each value not applied that changes the result is
    returned as an `amend_not_applied` flag naming it; restate it to apply it
    (`confirm_muscle`, or a follow-up amend for load_type).
    Changed values go through as changes.
    Mutates `exercises` (muscle_group / load_type / weight + unit) in place.
    Returns (keep, flags).
    """
    keep: list[dict | None] = []
    flags: list[AnomalyFlag] = []
    for ex, m in zip(exercises, _referenced(exercises, list(stored or []))):
        if m is None:
            keep.append(None)
            continue
        s, same = m
        restated_muscle = (ex.muscle_group is not None and not ex.confirm_muscle
                           and ex.muscle_group.value == s.get("muscle_group"))
        if (ex.unit is None and not ex.weight and ex.weight_kg
                and s.get("entered_unit") and s.get("entered_weight")
                and _same_loads(list(s.get("weight_kg") or []), ex.weight_kg)):
            ex.weight = list(s["entered_weight"])
            ex.unit = WeightUnit(s["entered_unit"])
        if not same:
            flags.extend(_not_carried_on_rename(ex, s, restated_muscle))
            keep.append(None)
            continue
        k: dict = {}
        if ex.name == s.get("name"):
            raw = s.get("raw_name")
            if raw is None and resolve_name(s["name"]) not in (None, s["name"]):
                # pre-0003: a name unknown back then was stored as typed, and
                # every old canonical name still resolves to itself — so a
                # stored name the catalog now maps elsewhere IS what was typed
                raw = s["name"]
            k["raw"] = raw
        if restated_muscle:
            k["source"] = s.get("muscle_source")
        if ex.load_type is None:
            if s.get("load_type"):
                ex.load_type = LoadType(s["load_type"])
            else:
                k["load_type_null"] = True
        keep.append(k)
    return keep, flags


def _not_carried_on_rename(ex, replaced: dict, restated_muscle: bool) -> list[AnomalyFlag]:
    """Drop values a renamed exercise copied from the one it replaces; one flag
    per value that changes the result. A muscle_group equal to the old
    exercise's is a copy whoever set it there — a caller-set one included —
    unless `confirm_muscle` restates it (a different muscle_group is the
    caller's own explicit choice and is kept)."""
    flags: list[AnomalyFlag] = []
    new = classify(ex.name)
    old_name = replaced.get("name") or "(nameless entry)"
    if restated_muscle:
        given = ex.muscle_group
        ex.muscle_group = None  # derived for the new name, like a fresh log
        if new.muscle_group is not given:
            was = ("set by the caller on" if replaced.get("muscle_source")
                   == MuscleSource.caller.value else "stored on")
            flags.append(AnomalyFlag(code=AnomalyCode.amend_not_applied, detail=(
                f"{new.name}: muscle_group {given.value} ({was} the replaced exercise "
                f"'{old_name}') was not applied to the new exercise; resend it with "
                "confirm_muscle: true if the user stated it")))
    default = default_load_type(new.name)
    if (ex.load_type is not None and ex.load_type.value == replaced.get("load_type")
            and ex.load_type is not default):
        given = ex.load_type
        ex.load_type = None  # the new identity's default (or unknown) applies
        flags.append(AnomalyFlag(code=AnomalyCode.amend_not_applied, detail=(
            f"{new.name}: load_type {given.value} matched the replaced exercise "
            f"'{old_name}' and was not applied ("
            + (f"default {default.value}" if default else "left unknown")
            + "); restate it in a follow-up amend if the user stated it")))
    return flags


def parse_amend_input(args: dict) -> SessionAmendInput:
    """Validate a coach_session_amend argument dict against the stored session.

    The stored session is handed to validation as context so an exercise
    restated UNCHANGED (and the stored date restated as is) is not re-checked
    against today's input rules: rows older code wrote can hold values the
    rules reject, and the coach must still be able to fix another exercise of
    that session (P58). Changed and new exercises are validated in full. An
    unknown session id falls through to plain validation (amend_session then
    reports it)."""
    context = None
    sid = args.get("session_id") if isinstance(args, dict) else None
    if sid is not None:
        snap = _snapshot(_uuid(sid))
        if snap is not None:
            context = {"stored_exercises": snap["exercises"] or [],
                       "stored_date": date.fromisoformat(snap["date"])}
    return SessionAmendInput.model_validate(args, context=context)


def _stored_flags(e: dict | None, index: int) -> list[AnomalyFlag]:
    """The anomaly flags of an exercise kept exactly as stored, derived from
    the stored values like coach_session_detail does."""
    e = e or {}
    name = e.get("name") or f"exercise {index}"
    flags: list[AnomalyFlag] = []
    if _needs_review(e.get("name"), e.get("muscle_source")):
        flags.append(AnomalyFlag(
            code=AnomalyCode.needs_review,
            detail=_review_text(e["name"], e.get("muscle_group"), e.get("muscle_source"))
            if e.get("name") else f"exercise entry {index} has no name"))
    if e.get("pain_flag"):
        flags.append(AnomalyFlag(code=AnomalyCode.pain_flag, detail=f"{name}: pain during exercise"))
    fq = e.get("form_quality")
    if fq is not None and fq < 3:
        flags.append(AnomalyFlag(
            code=AnomalyCode.form_quality_low,
            detail=f"{name}: form_quality={fq} (volume -50% downstream)"))
    return flags


def amend_session(data: SessionAmendInput) -> SessionChange:
    """Replace a session's date and exercises, keeping its id (and created_at).

    The new content goes through prepare_session(), like a fresh log,
    except that an exercise restating a stored one keeps its stored
    provenance (_provenance_to_keep). Fields left out or null (phase, kind,
    pre_recovery_score, post_feedback, label) keep their stored values; `clear`
    removes post_feedback / pre_recovery_score / label explicitly.
    """
    sid = data.session_id
    before = _snapshot(sid)
    if before is None:
        raise ValueError(f"no session with id {sid}")
    removed = _removed(before["exercises"], data.exercises)
    # exercises restated unchanged that today's input rules reject (legacy rows,
    # P58) are kept exactly as stored; everything else is prepared as a fresh log
    live = [ex for ex in data.exercises if not ex.keep_stored]
    keep, carry_flags = _provenance_to_keep(live, before["exercises"])
    # model_construct: kept stored values (a legacy date, post_feedback, ...) are
    # not re-checked; every value the caller sent was validated at parse time
    fresh = SessionInput.model_construct(
        date=data.date, exercises=live,
        phase=data.phase or (PhaseType(before["phase"]) if before["phase"] else None),
        kind=SessionKind(data.kind or before["kind"]),
        pre_recovery_score=_amended(data, "pre_recovery_score", before),
        post_feedback=_amended(data, "post_feedback", before),
        label=_amended(data, "label", before),
    )
    phase, prepared, flags = prepare_session(fresh, keep)
    flags = carry_flags + flags
    prepared_iter = iter(prepared)
    structs = []
    for ex in data.exercises:
        if ex.keep_stored:
            structs.append(dict(before["exercises"][ex.index]))
            flags = flags + _stored_flags(before["exercises"][ex.index], ex.index)
        else:
            structs.append(next(prepared_iter))

    def work():
        get_duckdb().execute(
            """UPDATE sessions SET date = ?, phase = ?, kind = ?, pre_recovery_score = ?,
                                   post_feedback = ?, exercises = ?, label = ?
               WHERE id = ?""",
            [fresh.date, phase.value, fresh.kind.value, fresh.pre_recovery_score,
             fresh.post_feedback, structs, fresh.label, sid])
        after = _snapshot(sid)
        if after == before:
            return None  # stored byte-identically: nothing to audit (P64a)
        return _audit(DecisionEventType.session_amend, sid, before,
                      f"amended: {len(before['exercises'] or [])} -> {len(structs)} exercise(s)",
                      after=after)

    audit_id = _in_transaction(work)
    if audit_id is None:
        return SessionChange(action="amended", session_id=sid, audit_id=None, changed=False,
                             anomaly_flags=flags, phase=phase.value, kind=fresh.kind.value,
                             pre_recovery_score=fresh.pre_recovery_score,
                             post_feedback=fresh.post_feedback, label=fresh.label,
                             message="nothing changed: the session is stored as it was "
                                     "(no audit entry written)")
    message = "session amended; previous version audited"
    if removed:
        message += (f"; removed {len(removed)} stored exercise(s) not restated "
                    "(see removed_exercises)")
    return SessionChange(action="amended", session_id=sid, audit_id=audit_id,
                         anomaly_flags=flags, removed_exercises=removed,
                         phase=phase.value, kind=fresh.kind.value,
                         pre_recovery_score=fresh.pre_recovery_score,
                         post_feedback=fresh.post_feedback, label=fresh.label,
                         message=message)


def _removed(stored: list | None, exercises: list) -> list[RemovedExercise]:
    """Every stored exercise no amend exercise restates by `index` — they are
    dropped from the row, so each is reported (nameless entries included)."""
    kept = {ex.index for ex in exercises if ex.index is not None}
    out = []
    for i, s in enumerate(stored or []):
        if i in kept:
            continue
        s = s or {}
        out.append(RemovedExercise(index=i, name=s.get("name"), raw_name=s.get("raw_name"),
                                   muscle_group=s.get("muscle_group"), sets=s.get("sets")))
    return out


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


def restore_snapshot(audit_id) -> SessionChange:
    """Put a session back exactly as an audit entry recorded it before the
    change (a deleted session is re-inserted; an amended one is overwritten).
    For recovery by the coding agent — deliberately not a coach tool.

    The restore is itself audited (`session_restore`, in the same transaction)
    so the audit chain has no gap: its payload holds the row it overwrote as
    `before` (None when the session did not exist), the restored row as
    `after`, and `restored_from`. Restoring that entry puts the overwritten
    state back — or, when nothing existed, deletes the session again.
    """
    aid = _uuid(audit_id, "audit_id")
    row = get_duckdb().execute(
        "SELECT event_type, payload FROM decision_log WHERE id = ?", [aid]).fetchone()
    if row is None or row[1] is None or row[0] not in {e.value for e in _PAST}:
        raise ValueError(f"no restorable audit entry {audit_id}")
    recorded = json.loads(row[1])
    before = recorded["before"]
    sid = UUID(recorded["session_id"])

    def work():
        d = get_duckdb()
        overwritten = _snapshot(sid)
        d.execute("DELETE FROM sessions WHERE id = ?", [sid])
        if before is not None:
            d.execute(
                """INSERT INTO sessions (id, date, phase, kind, pre_recovery_score, post_feedback,
                                         created_at, exercises, label)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [sid, before["date"], before["phase"], before["kind"],
                 before["pre_recovery_score"], before["post_feedback"], before["created_at"],
                 before["exercises"], before.get("label")])
        if before is None:
            note = "restored: session removed (the entry recorded none)"
        elif overwritten is None:
            note = "restored: session re-created"
        else:
            note = "restored: session overwritten"
        return _audit(DecisionEventType.session_restore, sid, overwritten, note,
                      after=_snapshot(sid), restored_from=aid)

    new_audit = _in_transaction(work)
    return SessionChange(action="restored", session_id=sid, audit_id=new_audit,
                         message="session restored; the state it replaced is in the audit trail")
