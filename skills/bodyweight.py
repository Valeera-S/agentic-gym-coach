"""bodyweight — the bodyweight time series (table `bodyweight_log`).

Nutrition ch02: every calorie number is a hypothesis, corrected only by
WEEKLY-AVERAGE bodyweight trends. That needs a series, and the series is only
meaningful like-for-like: a post-workout reading and a fasted morning reading
differ by more than a week of real change. So every reading carries a
condition, and `summarize` averages PER CONDITION — it never blends them — over
DAILY values (readings within a day are averaged first).

kg values in read outputs (readings, means) are rounded to 2 decimals; the
stored kg and the entered weight + unit stay exact.

Corrections (P74): amend_bodyweight / delete_bodyweight mirror the session tools:
the complete pre-change row goes to decision_log.payload in the same transaction.

Does not write profile.bodyweight_kg (profile writes need the user's confirmation).
"""

from __future__ import annotations

import json
from datetime import date
from uuid import UUID

from models.bodyweight import (
    BodyweightChange, BodyweightHistory, BodyweightInput, BodyweightReading,
    BodyweightSummary, ConditionAverage, WeighCondition,
)
from models.enums import DecisionEventType, WeightUnit

from .init import get_duckdb
from .trend_analysis import window_start

_COLS = "id, date, weight_kg, weight_entered, weight_unit, condition, scale, notes, created_at"


def _reading(r) -> BodyweightReading:
    return BodyweightReading(
        id=r[0], date=r[1], weight_kg=r[2], weight_entered=r[3],
        weight_unit=WeightUnit(r[4]) if r[4] else None,
        condition=WeighCondition(r[5]), scale=r[6], notes=r[7], created_at=r[8])


def log_bodyweight(inp: BodyweightInput) -> BodyweightReading:
    """Persist one reading (already validated by BodyweightInput)."""
    row = get_duckdb().execute(
        f"""INSERT INTO bodyweight_log
              (date, weight_kg, weight_entered, weight_unit, condition, scale, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING {_COLS}""",
        [inp.date, inp.weight_kg, inp.weight, inp.unit.value if inp.unit else None,
         inp.condition.value, inp.scale, inp.notes],
    ).fetchone()
    return _reading(row)


def list_readings(start: date, end: date) -> list[BodyweightReading]:
    """Readings with start <= date <= end, oldest first."""
    rows = get_duckdb().execute(
        f"SELECT {_COLS} FROM bodyweight_log WHERE date BETWEEN ? AND ? "
        "ORDER BY date, created_at, id", [start, end]).fetchall()
    # P64b: a read output shows kg to 2 decimals (an lb entry converts to float
    # noise like 66.00000000003666); the stored value and the entered value +
    # unit stay exact. log_bodyweight echoes the exact row it wrote.
    return [_reading(r).model_copy(update={"weight_kg": round(r[2], 2)}) for r in rows]


def summarize(start: date, end: date) -> BodyweightSummary:
    """Mean kg per condition over start..end inclusive, plus how many readings
    and how many distinct days stand behind it.

    The mean is of DAILY values: readings within one day are averaged first,
    then the days (Nutrition ch02 — weekly averages of daily weights), so two
    readings on one day weigh as one day, not two."""
    rows = get_duckdb().execute(
        "SELECT condition, sum(n), count(*), avg(day_mean) FROM ("
        "  SELECT condition, date, count(*) AS n, avg(weight_kg) AS day_mean "
        "  FROM bodyweight_log WHERE date BETWEEN ? AND ? GROUP BY condition, date) "
        "GROUP BY condition ORDER BY condition",
        [start, end]).fetchall()
    return BodyweightSummary(start=start, end=end, averages=[
        ConditionAverage(condition=WeighCondition(c), readings=int(n), days=int(d),
                              mean_kg=round(float(m), 2))
        for c, n, d, m in rows])


def fasted_mean_7d(end: date) -> float | None:
    """7-day mean of the daily morning_fasted values ending at `end`, or None."""
    for a in summarize(window_start(end, 7), end).averages:
        if a.condition is WeighCondition.morning_fasted:
            return a.mean_kg
    return None


def bodyweight_history(window_days: int, end: date) -> BodyweightHistory:
    start = window_start(end, window_days)
    return BodyweightHistory(
        window_days=window_days, start=start, end=end,
        readings=list_readings(start, end),
        last_7_days=summarize(window_start(end, 7), end),
        window=summarize(start, end))


# --- corrections: amend / delete (P74) ------------------------------------------------

_CLEARABLE = ("scale", "notes")


def _uuid(value) -> UUID:
    if isinstance(value, UUID):
        return value
    if value is None:
        raise ValueError("missing required argument 'id'")
    try:
        if isinstance(value, str):
            return UUID(value)
    except ValueError:
        pass
    shown = repr(value) if len(repr(value)) <= 60 else repr(value)[:57] + "...'"
    raise ValueError("id must be a UUID (as returned by coach_bodyweight_log / "
                     f"coach_bodyweight_history), got {shown}")


def _snapshot(rid: UUID) -> dict | None:
    """The stored row, JSON-ready and complete (every column)."""
    row = get_duckdb().execute(f"SELECT {_COLS} FROM bodyweight_log WHERE id = ?", [rid]).fetchone()
    if row is None:
        return None
    return {"id": str(row[0]), "date": row[1].isoformat(), "weight_kg": row[2],
            "weight_entered": row[3], "weight_unit": row[4], "condition": row[5],
            "scale": row[6], "notes": row[7],
            "created_at": row[8].isoformat() if row[8] is not None else None}


def _stored(rid: UUID) -> dict:
    snap = _snapshot(rid)
    if snap is None:
        raise ValueError(f"no bodyweight reading with id {rid} "
                         "(ids come from coach_bodyweight_history)")
    return snap


def _audit(event: DecisionEventType, rid: UUID, before: dict, note: str,
           after: dict | None = None) -> UUID:
    payload = {"bodyweight_id": str(rid), "before": before}
    if after is not None:
        payload["after"] = after
    past = "amended" if event is DecisionEventType.bodyweight_amend else "deleted"
    return get_duckdb().execute(
        """INSERT INTO decision_log (event_type, trigger_signal, reasoning_chain, payload)
           VALUES (?, ?, ?, ?) RETURNING id""",
        [event.value, f"bodyweight reading {rid} {past}", note,
         json.dumps(payload, ensure_ascii=False)],
    ).fetchone()[0]


def _merged_input(before: dict, args: dict) -> BodyweightInput:
    """The stored reading with the sent fields laid over it, validated exactly
    like a fresh log. A weight sent (weight + unit, or weight_kg) replaces the
    stored one wholesale, entered value + unit included."""
    clear = args.get("clear") or []
    if not isinstance(clear, list) or not all(isinstance(c, str) for c in clear):
        raise ValueError("clear must be a list of field names")
    for c in clear:
        if c not in _CLEARABLE:
            raise ValueError(f"clear can only name {' / '.join(_CLEARABLE)}, got {c!r}")
        if args.get(c) is not None:
            raise ValueError(f"{c} is both sent and named in clear")
    if args.get("date") is not None and not isinstance(args["date"], str):
        raise ValueError("date must be an ISO date string (YYYY-MM-DD), "
                         f"got {type(args['date']).__name__}")
    merged: dict = {
        "date": before["date"], "condition": before["condition"],
        "scale": None if "scale" in clear else before["scale"],
        "notes": None if "notes" in clear else before["notes"],
    }
    for key in ("date", "condition", "scale", "notes"):  # null = keep, like session amend
        if args.get(key) is not None:
            merged[key] = args[key]
    if any(args.get(k) is not None for k in ("weight", "unit", "weight_kg")):
        merged.update({k: args[k] for k in ("weight", "unit", "weight_kg") if args.get(k) is not None})
    elif before["weight_entered"] is not None:
        merged.update(weight=before["weight_entered"], unit=before["weight_unit"],
                      weight_kg=before["weight_kg"])
    else:
        merged["weight_kg"] = before["weight_kg"]
    return BodyweightInput.model_validate(merged)


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


def amend_bodyweight(args: dict) -> BodyweightChange:
    """Correct one reading: fields omitted (or null) keep their stored value;
    the result is validated like a fresh log before anything is written. An
    amend that leaves the row as it was writes no audit entry (`changed: false`)."""
    rid = _uuid(args.get("id"))
    before = _stored(rid)
    inp = _merged_input(before, args)

    def work():
        get_duckdb().execute(
            """UPDATE bodyweight_log SET date = ?, weight_kg = ?, weight_entered = ?,
                   weight_unit = ?, condition = ?, scale = ?, notes = ? WHERE id = ?""",
            [inp.date, inp.weight_kg, inp.weight, inp.unit.value if inp.unit else None,
             inp.condition.value, inp.scale, inp.notes, rid])
        after = _snapshot(rid)
        if after == before:
            return None
        return _audit(DecisionEventType.bodyweight_amend, rid, before,
                      f"amended reading dated {before['date']}", after=after)

    audit_id = _in_transaction(work)
    reading = _reading(get_duckdb().execute(
        f"SELECT {_COLS} FROM bodyweight_log WHERE id = ?", [rid]).fetchone())
    if audit_id is None:
        return BodyweightChange(action="amended", id=rid, audit_id=None, changed=False,
                                reading=reading,
                                message="nothing changed: the reading is stored as it was "
                                        "(no audit entry written)")
    return BodyweightChange(action="amended", id=rid, audit_id=audit_id, reading=reading,
                            message="reading amended; previous version audited")


def delete_bodyweight(reading_id) -> BodyweightChange:
    """Hard-delete one reading; its complete row is audited first."""
    rid = _uuid(reading_id)
    before = _stored(rid)
    row = get_duckdb().execute(f"SELECT {_COLS} FROM bodyweight_log WHERE id = ?", [rid]).fetchone()

    def work():
        audit_id = _audit(DecisionEventType.bodyweight_delete, rid, before,
                          f"deleted reading dated {before['date']}")
        get_duckdb().execute("DELETE FROM bodyweight_log WHERE id = ?", [rid])
        return audit_id

    audit_id = _in_transaction(work)
    return BodyweightChange(action="deleted", id=rid, audit_id=audit_id, reading=_reading(row),
                            message="reading deleted; its full row is in the audit trail")
