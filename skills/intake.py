"""intake — the standardized assessment scanner (one checklist, one mode).

assess_intake() scans stored state (user_profiles payload, injury_status,
session history) against models.intake.INTAKE_CHECKLIST and reports, per
field, what is collected vs missing, plus per-domain readiness. It NEVER
writes and never invents values — the coach cites collected values (asking
"still accurate?"), asks for missing ones in checklist order, and applies
the soft per-domain gates: no training/nutrition plan volunteered without
its gating data; a provisional plan with explicit limitations only when the
user insists (COACH_PROMPT "Standardized intake assessment").

There are no modes: the same scan serves a first-ever user (everything
missing), a returning user (old values cited back for confirmation), and a
mid-program check. weeks_since_last_session (threshold: the labeled
heuristic skills.snapshot.REASSESSMENT_GAP_WEEKS) is the staleness nudge
for the confirm-present step, not a switch.

Contract: deterministic, offline, read-only.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from models import (
    INTAKE_CHECKLIST,
    INTAKE_ROUND_TITLES,
    DomainProgress,
    FieldReport,
    FieldStatus,
    GateCondition,
    GateDomain,
    IntakeField,
    IntakeReport,
    NextRound,
    UserProfile,
)

from .injuries import InjuryStatus, list_injuries
from .profile import get_profile
from .snapshot import session_gap


def _is_present(value: Any) -> bool:
    """Presence = a real answer was stored. False/0 are answers; blanks aren't."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True  # bools, ints, floats, enums, dates — a stored value is an answer


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "model_dump"):  # pydantic (Goal, ...)
        return value.model_dump(mode="json")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _stored_value(field: IntakeField, profile: UserProfile | None,
                  injuries: list[InjuryStatus]) -> tuple[Any, bool]:
    """Resolve one checklist field → (raw value, present?).

    Presence semantics are declared on the field (empty_means_missing), not
    dispatched by name: a list-typed profile field holding a list is present
    whenever a profile exists (empty = valid "nothing applies" answer) unless
    the field sets empty_means_missing — goals and weekly_availability — where
    empty is indistinguishable from never-asked. A list field that is None was
    never asked (concurrent_sports, P52) and is missing.
    """
    if field.storage == "injury_status":
        return injuries, _is_present(injuries)
    if not field.storage.startswith("profile."):
        raise ValueError(f"checklist storage path not understood: {field.storage}")
    value = getattr(profile, field.storage.split(".", 1)[1]) if profile else None
    if isinstance(value, (list, tuple)) and not field.empty_means_missing:
        return value, profile is not None
    return value, _is_present(value)


def _applies(field: IntakeField, profile: UserProfile | None) -> bool:
    """Does this field's gate_condition hold for THIS profile? Independent of
    whether the field is collected (progress totals need it for both)."""
    cond = field.gate_condition
    if cond is GateCondition.unless_male:
        return profile is None or profile.sex is None or profile.sex.value != "male"
    if cond is GateCondition.when_cutting:
        return profile is not None and any(
            g.kind.value == "fat_loss" or (g.physique_target and g.physique_target.value == "ripped")
            for g in profile.goals)
    return True


def _blocks(field: IntakeField, profile: UserProfile | None) -> bool:
    """Does this field, if missing, hold its gate open for THIS profile?"""
    return field.blocks_gate and _applies(field, profile)


def _gates(field: IntakeField, domain: str) -> bool:
    return field.gates in (GateDomain(domain), GateDomain.both)


def _progress(reports: list[FieldReport], profile: UserProfile | None) -> dict[str, DomainProgress]:
    """done/total per domain over the fields that gate it right now: blocking
    fields whose gate_condition applies to the stored profile."""
    out: dict[str, DomainProgress] = {}
    for domain in ("training", "nutrition"):
        counted = [r for r in reports if _blocks(r, profile) and _gates(r, domain)]
        out[domain] = DomainProgress(
            done=sum(r.status is FieldStatus.collected for r in counted), total=len(counted))
    return out


def _next_round(reports: list[FieldReport]) -> tuple[int, NextRound | None]:
    """(rounds_total, lowest guided round still pending). A round is done when
    every field in it that blocks NOW is collected; a pending round lists all its
    missing fields (blocking or not), a done round is never offered — so a user
    with no injury rows is not held in round 3 by the non-blocking injuries field."""
    rounds = sorted({r.round for r in reports if r.round is not None})
    for rnd in rounds:
        members = [r for r in reports if r.round == rnd]
        if any(r.blocks_now for r in members):
            return len(rounds), NextRound(
                round=rnd, title=INTAKE_ROUND_TITLES.get(rnd, ""),
                fields=[r.name for r in members if r.status is FieldStatus.missing])
    return len(rounds), None


def assess_intake(today: date | None = None) -> IntakeReport:
    """Scan stored state against the bucket list. Read-only; never guesses."""
    today = today or date.today()
    profile = get_profile()
    injuries = list_injuries()
    gap = session_gap(today)

    reports: list[FieldReport] = []
    for f in INTAKE_CHECKLIST:
        value, present = _stored_value(f, profile, injuries)
        reports.append(FieldReport(
            **f.model_dump(),
            status=FieldStatus.collected if present else FieldStatus.missing,
            value=_json_safe(value) if present else None,
            blocks_now=(not present) and _blocks(f, profile),
        ))

    missing_by_gate: dict[str, list[str]] = {"training": [], "nutrition": []}
    for r in reports:
        if not r.blocks_now:
            continue
        if r.gates in (GateDomain.training, GateDomain.both):
            missing_by_gate["training"].append(r.name)
        if r.gates in (GateDomain.nutrition, GateDomain.both):
            missing_by_gate["nutrition"].append(r.name)

    rounds_total, next_round = _next_round(reports)
    return IntakeReport(
        fields=reports,
        weeks_since_last_session=gap.weeks_since_last_session,
        training_ready=not missing_by_gate["training"],
        nutrition_ready=not missing_by_gate["nutrition"],
        missing=[r.name for r in reports if r.status is FieldStatus.missing],
        missing_by_gate=missing_by_gate,
        progress=_progress(reports, profile),
        rounds_total=rounds_total,
        next_round=next_round,
    )
