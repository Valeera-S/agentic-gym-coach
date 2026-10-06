"""session_templates — "same as last time" for low-friction logging (UX3).

session_template() turns a stored session into an exact coach_log_session
payload; session_labels() lists the labels in use with their gaps. Labels
match through exercise_catalog.lookup_key (the one normalization owner).
Pure, deterministic, read-only.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from models import LabelSummary, SessionTemplate
from models.exercise_catalog import lookup_key

from .init import get_duckdb
from .sessions import get_session_detail

# newest first; ties broken like the session listing
_LABELED = ("SELECT CAST(id AS VARCHAR), label, date, kind FROM sessions "
            "WHERE label IS NOT NULL ORDER BY date DESC, created_at DESC, id")


def _payload(ex) -> dict:
    out: dict = {"name": ex.name, "sets": ex.sets}
    if any(r is not None for r in ex.reps):
        out["reps"] = list(ex.reps)
    if ex.unit_as_entered is not None:
        out["weight"] = list(ex.weight_as_entered)
        out["unit"] = ex.unit_as_entered
    if ex.load_type is not None:
        out["load_type"] = ex.load_type
    if ex.tempo is not None:
        out["tempo"] = ex.tempo
    if ex.muscle_source == "caller" and ex.muscle_group is not None:
        out["muscle_group"] = ex.muscle_group
    return out


def session_template(label: str | None = None,
                     session_id: str | UUID | None = None) -> SessionTemplate:
    """Exactly one of label / session_id. ValueError (invalid_input) otherwise,
    for an unknown id, or for an unknown label (the message lists the known ones)."""
    if (label is None) == (session_id is None):
        raise ValueError("give exactly one of label or session_id")
    if label is not None:
        key = lookup_key(label)
        rows = get_duckdb().execute(_LABELED).fetchall()
        match = next((r for r in rows if lookup_key(r[1]) == key), None)
        if match is None:
            known = sorted({r[1] for r in rows}, key=str.lower)
            raise ValueError(f"no session labeled '{label}'; labels in use: "
                             + (", ".join(known) if known else "none yet"))
        session_id = match[0]
    detail = get_session_detail(session_id=session_id)[0]
    exercises, skipped = [], []
    for ex in detail.exercises:
        if ex.name is None or not ex.sets:
            skipped.append(f"entry {ex.index}: no name or no sets (older row)")
            continue
        exercises.append(_payload(ex))
    return SessionTemplate(source_session_id=detail.id, source_date=detail.date,
                           label=detail.label, kind=detail.kind,
                           exercises=exercises, skipped=skipped)


def session_labels(today: date | None = None) -> list[LabelSummary]:
    """Labels in use, most recently used first."""
    today = today or date.today()
    groups: dict[str, LabelSummary] = {}
    for _sid, label, d, kind in get_duckdb().execute(_LABELED).fetchall():
        key = lookup_key(label)
        if key in groups:  # rows come newest first: the first one names the group
            groups[key].count += 1
            continue
        groups[key] = LabelSummary(label=label, kind=kind, count=1, last_date=d,
                                   days_without_entry=max((today - d).days, 0))
    return list(groups.values())
