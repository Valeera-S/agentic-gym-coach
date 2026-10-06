"""phase — the single resolver of "what phase are we in".

One definition, previously copy-pasted in three places with diverging
fallbacks (orchestrator / snapshot / session_logger):

    current_phase(): the phase of the most recent TRAINING session (habit
    sessions never decide it) → only when there is no training session at all,
    the latest phase_snapshots row → None (no data at all).

    "Most recent" is by (date, created_at, id), newest first, so a session
    backfilled with an older date never moves the phase, and same-day sessions
    resolve by creation order. The snapshot row is deliberately NOT read first:
    generate_phase_snapshot() writes current_phase() back into that table, so
    reading it first froze the phase after the first snapshot (P45).

    phase_for_logging(): the phase to TAG a new session with — explicit user
    input wins, then current_phase(), then `maintenance` (the goal-agnostic
    off-season default, Training ch04). The fallback keeps the user from
    ever having to tag a phase by hand.
"""

from __future__ import annotations

from models import PhaseType

from .init import get_duckdb


def current_phase() -> PhaseType | None:
    """Phase of the most recent training session, else the latest snapshot's
    phase (only when no training session exists), else None.

    A legacy training session with a NULL phase is skipped (it states no
    phase); the next most recent one decides."""
    row = get_duckdb().execute(
        "SELECT phase FROM sessions WHERE kind = 'training' AND phase IS NOT NULL "
        "ORDER BY date DESC, created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row and row[0]:
        return PhaseType(row[0])
    # a session that exists but carries no phase still beats a stale snapshot
    if get_duckdb().execute(
            "SELECT 1 FROM sessions WHERE kind = 'training' LIMIT 1").fetchone():
        return None
    row = get_duckdb().execute(
        "SELECT phase FROM phase_snapshots ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    return PhaseType(row[0]) if row and row[0] else None


def phase_for_logging(explicit: PhaseType | None = None) -> PhaseType:
    """Phase to persist on a new session: explicit > current > maintenance."""
    if explicit is not None:
        return explicit
    return current_phase() or PhaseType.maintenance
