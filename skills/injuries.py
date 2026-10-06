"""injuries — the single owner of the injury_status table (safety source of truth).

Every read and write of injury_status goes through here; no other module
knows the table's schema (previously spread across five modules). Writes are
validated and canonicalized:

- location/status/severity are constructed as models.InjuryStatus, so an
  out-of-vocabulary value or out-of-range severity fails at write time with
  a standard validation error — instead of being stored and crashing a later
  Tier-1 load far from the faulty write (adversarial F2/F9).
- every contraindicated/alternative exercise name is canonicalized via the
  exercise catalog, so a ban written as the user said it is stored under the
  same name the safety gate queries. Names the catalog can't map are stored
  verbatim but returned in `needs_review` — the coach must confirm them with
  the user before trusting the gate on them (the known fail-open residual,
  adversarial F1 / audit Q4).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from models import InjurySeedResult, InjuryState, InjuryStatus, PainLocation, canonicalize
from models.exercise_catalog import ban_match_names, lookup_key, resolve_name

from .init import get_duckdb

_COLS = "location, status, severity, contraindicated_exercises, safe_alternatives"


def _row_to_model(row: tuple, *, with_id: bool = False) -> InjuryStatus:
    loc, st, sev, contra, alts = row[-5:]
    return InjuryStatus(
        id=row[0] if with_id else None,
        location=PainLocation(loc),
        status=InjuryState(st),
        severity=sev,
        contraindicated_exercises=[c for c in (contra or []) if c is not None],
        safe_alternatives=[a for a in (alts or []) if a is not None],
    )


def _canonicalize_names(names: list[str]) -> tuple[list[str], list[str]]:
    """Map names through the exercise catalog → (stored, needs_review)."""
    stored, review = [], []
    for name in names:
        can, _mg, needs_review = canonicalize(name)
        stored.append(can)
        if needs_review:
            review.append(can)
    return stored, review


def _name_list(field: str, value: Any) -> list[str]:
    """Strict exercise-name list: a real list of non-blank strings, else ValueError.

    A bare string must never reach list() (it would be stored as one ban per
    character and the real ban silently lost), and a blank entry would be an
    empty-string ban that matches nothing — both fail-open shapes (P35).
    Raised before any write; surfaces as `invalid_input`.
    """
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list of exercise names, got {type(value).__name__}")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} must contain only non-blank strings, got {item!r}")
    return list(value)


def list_injuries(active_only: bool = False) -> list[InjuryStatus]:
    """Injury rows as models, newest first (`active_only` skips resolved)."""
    sql = f"SELECT id, {_COLS} FROM injury_status"
    if active_only:
        sql += " WHERE status <> 'resolved'"
    sql += " ORDER BY updated_at DESC"
    return [_row_to_model(r, with_id=True) for r in get_duckdb().execute(sql).fetchall()]


def get_active_injuries() -> list[InjuryStatus]:
    """Non-resolved injuries — the Tier-1 working-memory slice."""
    return list_injuries(active_only=True)


def seed_injury(location: str, status: str, severity: int,
                contraindicated_exercises: list[str] | None = None,
                safe_alternatives: list[str] | None = None) -> InjurySeedResult:
    """Validate, canonicalize, and insert one injury row.

    Raises pydantic ValidationError on out-of-vocabulary location/status or
    out-of-range severity, and ValueError for a malformed name list or a
    safe_alternative the row's own bans would block (P33) — nothing is written. Canonical alias hits are
    stored under the canonical name; unmapped names are stored verbatim and
    listed in `needs_review` for user confirmation.
    """
    injury = InjuryStatus(
        location=location, status=status, severity=severity,
        contraindicated_exercises=_name_list("contraindicated_exercises", contraindicated_exercises),
        safe_alternatives=_name_list("safe_alternatives", safe_alternatives),
    )
    stored_contra, review = _canonicalize_names(injury.contraindicated_exercises)
    stored_alts, alt_review = _canonicalize_names(injury.safe_alternatives)
    self_banned = [a for a in stored_alts if blocked_by(a, stored_contra)]
    if self_banned:
        raise ValueError(
            "safe_alternatives that this row's own contraindicated_exercises "
            f"would block: {', '.join(self_banned)} — offer only exercises that "
            "pass the gate; nothing was written")

    get_duckdb().execute(
        f"INSERT INTO injury_status ({_COLS}) VALUES (?, ?, ?, ?, ?)",
        [injury.location.value, injury.status.value, injury.severity,
         stored_contra, stored_alts],
    )
    stored = injury.model_copy(update={
        "contraindicated_exercises": stored_contra,
        "safe_alternatives": stored_alts,
    })
    return InjurySeedResult(injury=stored, needs_review=sorted(set(review + alt_review)))


def _ban_keys(entries: Iterable[str | None]) -> set[str]:
    """Lookup keys a list of stored ban entries covers: each entry's own text
    and its current canonical identity (case/whitespace-insensitive)."""
    keys: set[str] = set()
    for entry in entries:
        if entry is None:  # not producible by seed_injury; skip, don't crash the gate
            continue
        keys.add(lookup_key(entry))
        canon = resolve_name(entry)
        if canon:
            keys.add(lookup_key(canon))
    return keys


def blocked_by(name: str, ban_entries: Iterable[str | None]) -> bool:
    """True when a ban list blocks `name` — the ONE definition of "banned",
    shared by the gate, its alternatives filter and the seed-time check.
    Resolves aliases, legacy names and generic<->variant exactly as the gate
    does (exercise_catalog.ban_match_names)."""
    wanted = {lookup_key(n) for n in ban_match_names(name)}
    return bool(_ban_keys(ban_entries) & wanted)


def _live_rows() -> list[tuple[str, str, list[str], list[str]]]:
    """Non-resolved injury rows (newest first) as (location, status, bans, alts)."""
    rows = get_duckdb().execute(
        """
        SELECT location, status, contraindicated_exercises, safe_alternatives
        FROM injury_status
        WHERE status <> 'resolved'
        ORDER BY updated_at DESC, rowid DESC
        """
    ).fetchall()
    return [(loc, st, list(contra or []), [a for a in (alts or []) if a is not None])
            for loc, st, contra, alts in rows]


def contraindication_hits(names: Iterable[str]) -> list[tuple[str, str, list[str]]]:
    """Non-resolved injury rows (newest first) that ban any of `names`.

    The safety gate's only sanctioned read of injury_status. A stored ban
    entry matches if its own text OR its current canonical identity equals one
    of `names`, case/whitespace-insensitively — so a ban stored as the user
    said it ("skull crusher"), stored verbatim before its name entered the
    catalog ("Pec Deck"), or stored under a pre-split canonical name all keep
    blocking. `names` comes from exercise_catalog.ban_match_names().
    """
    wanted = {lookup_key(n) for n in names}
    return [(loc, st, alts) for loc, st, contra, alts in _live_rows()
            if _ban_keys(contra) & wanted]


def unbanned(candidates: Iterable[str]) -> list[str]:
    """`candidates` (order kept, duplicates dropped) minus every name that ANY
    current ban blocks. The gate offers only what passes the gate itself —
    a stored alternative list can go stale when a later row bans one of them (P33)."""
    all_bans = [e for _l, _s, contra, _a in _live_rows() for e in contra]
    out, seen = [], set()
    for name in candidates:
        key = lookup_key(name)
        if key in seen or blocked_by(name, all_bans):
            continue
        seen.add(key)
        out.append(name)
    return out


def tendon_summary() -> dict[str, Any]:
    """{location: {status, severity}} for non-resolved injuries (snapshot anchor)."""
    rows = get_duckdb().execute(
        "SELECT location, status, severity FROM injury_status WHERE status <> 'resolved'"
    ).fetchall()
    return {loc: {"status": st, "severity": sev} for loc, st, sev in rows}
