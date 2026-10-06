"""profile — validated user profile: goals, training age, schedule, priorities.

The profile is the v2 replacement for v1's hardcoded user assumptions: the
orchestrator derives priority muscles from it, the snapshot targets it, and
goal changes are audited to decision_log (a goal change is the biggest plan
modification there is).

Storage: append-only rows in user_profiles (latest row = current). Every
set_profile() writes a new row, so goal history is preserved ("why did we
prioritize X in June?").

Contract: deterministic, offline. get_profile() returns None when no profile
exists — the intake scan then reports everything missing; never invent defaults.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from models import DecisionEventType, Goal, UserProfile

from .init import get_duckdb


def get_profile() -> UserProfile | None:
    """Latest profile row, or None (⇒ the intake scan reports everything missing)."""
    row = get_duckdb().execute(
        "SELECT payload FROM user_profiles ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    # context: rebuilt from what is stored; input-only hygiene rules do not apply
    return UserProfile.model_validate_json(row[0], context={"stored": True})


def set_profile(profile: UserProfile) -> UserProfile:
    """Append a new profile version; audit goal changes to decision_log."""
    prev = get_profile()
    profile = profile.model_copy(update={"updated_at": datetime.now(timezone.utc)})
    get_duckdb().execute(
        "INSERT INTO user_profiles (updated_at, payload) VALUES (?, ?)",
        [profile.updated_at, profile.model_dump_json()],
    )
    change = describe_goal_change(prev.goals, profile.goals) if prev is not None else None
    if change is not None:
        get_duckdb().execute(
            """
            INSERT INTO decision_log (event_type, trigger_signal, reasoning_chain,
                                      alternative_rejected, future_validation_tag)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                DecisionEventType.goal_change.value,
                f"goal change: {change}",
                "user-declared goal update during coach session",
                "keeping prior goals against user intent",
                "re-run phase snapshot 4 weeks after change; compare trend",
            ],
        )
    return profile


def merge_profile(incoming: UserProfile, fields: set[str]) -> UserProfile:
    """Merge `fields` of an already validated `incoming` profile into the stored
    one and store the result (P71): a field named in `fields` overwrites, one not
    named keeps its stored value. `goals`, when named, replaces the whole goal
    list (audited by set_profile exactly as before). With no stored profile the
    incoming one is stored as is. The stored profile is read leniently, so an
    old value that today's input rules reject never blocks editing another
    field. A result that would be an all-default profile is refused, like an
    empty first profile (it would silently disarm the intake's empty-profile
    signal)."""
    prev = get_profile()
    merged = incoming if prev is None else prev.model_copy(
        update={k: getattr(incoming, k) for k in fields})
    if merged.model_copy(update={"updated_at": None}) == UserProfile():
        raise ValueError("profile would be empty — provide at least one field "
                         "(goals, training_age, days_per_week, bodyweight_kg, ...)")
    return set_profile(merged)


def derive_priority_muscles(profile: UserProfile | None) -> list:
    """Priority muscles: declared > goal targets > [] (balanced programming).

    Returns MuscleGroup values in stable order (declared order first, then
    any goal target muscles not already present).
    """
    if profile is None:
        return []
    out = list(profile.priority_muscles)
    for g in profile.goals:
        for m in g.target_muscles:
            if m not in out:
                out.append(m)
    return out


# Goal comparison rule (P51). A goal's identity is its FULL content: kind,
# physique_target, target_muscles, metric, deadline, notes. The goal list is
# compared as a multiset: pure reordering (of goals, or of a goal's
# target_muscles, which are an unordered set) is NOT a change; adding or
# removing a copy of an identical goal IS. Any other difference writes one
# audit entry; no difference writes none. The first profile ever stored has no
# prior goals to change, so it writes no entry.
_GOAL_FIELDS = ("physique_target", "target_muscles", "metric", "deadline", "notes")


def _goal_sig(g: Goal) -> tuple:
    return (
        g.kind.value,
        g.physique_target.value if g.physique_target else None,
        tuple(sorted({m.value for m in g.target_muscles})),
        g.metric,
        g.deadline.isoformat() if g.deadline else None,
        g.notes,
    )


def _fmt(v) -> str:
    if v is None or v == ():
        return "none"
    if isinstance(v, tuple):
        return "[" + ", ".join(v) + "]"
    return str(v)


def _fmt_goal(sig: tuple) -> str:
    extras = [f"{f}={_fmt(v)}" for f, v in zip(_GOAL_FIELDS, sig[1:]) if v not in (None, ())]
    return f"{sig[0]}({', '.join(extras)})" if extras else sig[0]


def describe_goal_change(prev: list[Goal], new: list[Goal]) -> str | None:
    """None when the goal lists are the same (order aside); else a precise
    description: goals added, goals removed, and for a goal whose kind stays
    but whose fields differ, each changed field from -> to."""
    before, after = Counter(map(_goal_sig, prev)), Counter(map(_goal_sig, new))
    if before == after:
        return None
    # key=repr: signatures mix None and str, which tuples cannot order directly
    removed = sorted((before - after).elements(), key=repr)
    added = sorted((after - before).elements(), key=repr)
    changed: list[str] = []
    # pair a removed goal with an added one of the same kind (fewest differing
    # fields first) so an edit reads as an edit, not as remove + add
    for r in list(removed):
        cands = [a for a in added if a[0] == r[0]]
        if not cands:
            continue
        a = min(cands, key=lambda c: sum(x != y for x, y in zip(c, r)))
        removed.remove(r)
        added.remove(a)
        diffs = [f"{f} {_fmt(rv)} -> {_fmt(av)}"
                 for f, rv, av in zip(_GOAL_FIELDS, r[1:], a[1:]) if rv != av]
        changed.append(f"{r[0]}: " + "; ".join(diffs))
    parts = []
    if removed:
        parts.append("removed=" + repr([
            _fmt_goal(r) + (" (a duplicate; a copy remains)" if r in after else "")
            for r in removed]))
    if added:
        parts.append("added=" + repr([
            _fmt_goal(a) + (" (duplicate of an existing goal)" if a in before else "")
            for a in added]))
    if changed:
        parts.append("changed=" + repr(changed))
    return " ".join(parts)
