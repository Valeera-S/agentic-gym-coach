"""Conformance: exercise_catalog vs the ch03 counting chart it cites.

`models/exercise_catalog.py` claims `SECONDARY_OVERLAP` implements Helms'
overlap doctrine (Training ch03). These tests hold it to that claim, so a
deviation cannot appear — or silently persist — without someone recording why.

The suite is deliberately a *ratchet*: `KNOWN_DEVIATIONS` is the accepted
broken state and must match exactly. A new deviation fails; fixing one also
fails, which is the signal to shrink the list.
"""

from __future__ import annotations

import pytest

from models.doctrine_ch03 import (
    BOOK_TO_REPO,
    CH03_COUNTING_CHART,
    EXERCISE_PATTERN,
    KNOWN_DEVIATIONS,
    BookMuscle,
    Fidelity,
    MovementPattern,
    UnclassifiedExerciseError,
    compute_deviations,
)
from models.enums import MuscleGroup
from models.exercise_catalog import SECONDARY_OVERLAP


def test_every_catalog_entry_is_classified():
    """A new exercise must declare its movement pattern — the one thing the
    chart needs in order to derive its muscles."""
    unclassified = sorted(set(SECONDARY_OVERLAP) - set(EXERCISE_PATTERN))
    assert not unclassified, (
        "catalog entries with no ch03 movement pattern: "
        f"{unclassified}. Add them to EXERCISE_PATTERN."
    )


def test_no_pattern_assigned_to_a_missing_entry():
    """Guards the other direction: a renamed/removed catalog entry leaving a
    stale assignment behind."""
    orphaned = sorted(set(EXERCISE_PATTERN) - set(SECONDARY_OVERLAP))
    assert not orphaned, (
        f"EXERCISE_PATTERN references entries the catalog no longer has: {orphaned}"
    )


def test_chart_is_fully_bridged():
    """Every muscle the chart names must have a verdict in BOOK_TO_REPO —
    including `None`, which is how the vocabulary gap stays visible."""
    assert set(BOOK_TO_REPO) == set(BookMuscle)
    for row in CH03_COUNTING_CHART.values():
        for muscle in row.muscles:
            assert muscle in BOOK_TO_REPO, f"{muscle} used by a row but unbridged"


def test_inexact_mappings_carry_a_reason():
    """`approximate` and `missing` are acceptable; unexplained ones are not.

    AGENTS.md: every field is book-cited or labelled. A silent approximation
    is exactly the defect this module exists to surface.
    """
    for muscle, (mapped, fidelity, why) in BOOK_TO_REPO.items():
        if fidelity is Fidelity.exact:
            assert mapped is not None, f"{muscle} marked exact but maps to nothing"
            continue
        assert why.strip(), f"{muscle} is {fidelity.value} but carries no explanation"


def test_isolation_entries_have_no_secondaries():
    """The chart's Isolation row credits the target muscle only."""
    offenders = {
        name: sorted(m.value for m in SECONDARY_OVERLAP[name])
        for name, pattern in EXERCISE_PATTERN.items()
        if pattern is MovementPattern.isolation and SECONDARY_OVERLAP.get(name)
    }
    assert not offenders, f"isolation exercises crediting secondaries: {offenders}"


def _ratchet_problems(actual: dict[str, str], known: dict[str, str]) -> list[str]:
    """Every way `actual` can disagree with the recorded baseline."""
    appeared = {k: v for k, v in actual.items() if k not in known}
    disappeared = sorted(set(known) - set(actual))
    changed = {
        k: (known[k], v)
        for k, v in actual.items()
        if k in known and known[k] != v
    }

    problems = []
    if appeared:
        problems.append(f"NEW deviations from ch03: {appeared}")
    if disappeared:
        problems.append(
            f"deviations FIXED but still listed: {disappeared} — remove them "
            "from KNOWN_DEVIATIONS"
        )
    if changed:
        problems.append(f"deviation details changed: {changed}")
    return problems


def test_deviations_match_the_recorded_baseline():
    """The ratchet."""
    problems = _ratchet_problems(compute_deviations(), KNOWN_DEVIATIONS)
    assert not problems, "\n".join(problems)


# --- the ratchet's own teeth -------------------------------------------------
# A ratchet that never fails is indistinguishable from no test at all, so each
# failure direction is exercised against a deliberately broken catalog.

def test_ratchet_catches_an_injected_deviation(monkeypatch):
    # Lateral Raise is isolation; giving it a secondary breaks the chart.
    monkeypatch.setitem(SECONDARY_OVERLAP, "Lateral Raise", [MuscleGroup.triceps])
    problems = _ratchet_problems(compute_deviations(), KNOWN_DEVIATIONS)
    assert len(problems) == 1
    assert problems[0].startswith("NEW deviations") and "Lateral Raise" in problems[0]


def test_ratchet_catches_a_fixed_but_still_listed_deviation(monkeypatch):
    # Row's only deviation is the missing middle-delt credit; supply it.
    assert "Row" in KNOWN_DEVIATIONS
    monkeypatch.setitem(
        SECONDARY_OVERLAP, "Row", [*SECONDARY_OVERLAP["Row"], MuscleGroup.side_delt]
    )
    problems = _ratchet_problems(compute_deviations(), KNOWN_DEVIATIONS)
    assert len(problems) == 1
    assert "FIXED but still listed" in problems[0] and "'Row'" in problems[0]


def test_ratchet_catches_a_changed_deviation_detail(monkeypatch):
    # Still deviating, but differently: the baseline text must be regenerated.
    monkeypatch.setitem(
        SECONDARY_OVERLAP, "Row", [*SECONDARY_OVERLAP["Row"], MuscleGroup.quads]
    )
    problems = _ratchet_problems(compute_deviations(), KNOWN_DEVIATIONS)
    assert len(problems) == 1 and "details changed" in problems[0]


def test_unclassified_entry_fails_with_a_clear_message(monkeypatch):
    monkeypatch.setitem(SECONDARY_OVERLAP, "Zercher Squat", [])
    monkeypatch.setitem(SECONDARY_OVERLAP, "Arnold Press", [])
    with pytest.raises(UnclassifiedExerciseError) as exc:
        compute_deviations()
    msg = str(exc.value)
    # Names every offender (not just the first) and says where to fix it.
    assert "Arnold Press" in msg and "Zercher Squat" in msg
    assert "EXERCISE_PATTERN" in msg


@pytest.mark.parametrize("name", sorted(KNOWN_DEVIATIONS))
def test_known_deviation_is_a_real_catalog_entry(name):
    """Stops the baseline rotting into a list of names nothing matches."""
    assert name in SECONDARY_OVERLAP, f"{name} is in KNOWN_DEVIATIONS but not the catalog"
