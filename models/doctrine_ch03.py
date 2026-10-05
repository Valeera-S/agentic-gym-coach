"""The ch03 counting chart, transcribed verbatim — conformance spec for the catalog.

`exercise_catalog.py` claims its `SECONDARY_OVERLAP` implements Helms' overlap
doctrine (Muscle & Strength Pyramid: Training ch03). That claim was added on
2026-09-09 (`b783ae3`, the commit that vendored the books) to a table written
on 2026-07-13 (`db2c8f7`), whose original comment labelled its first entry
`# Upper chest (specialization target)`. The vocabulary was never re-derived
from the book it now cites.

This module holds the book side of that comparison so the gap is *generated*
rather than eyeballed:

    CH03_COUNTING_CHART   the book's ten movement-pattern rows, verbatim
    BOOK_TO_REPO          book muscle -> MuscleGroup, with fidelity marked
    EXERCISE_PATTERN      each catalog canonical name -> its pattern
    KNOWN_DEVIATIONS      the accepted-as-broken state, one reason each

`tests/test_skills/test_catalog_doctrine.py` asserts the computed deviation
set equals `KNOWN_DEVIATIONS` exactly, so a new deviation fails the build and
a *fixed* one also fails (telling you to shrink the list).

Nothing here is invented doctrine. A pattern the book does not cover is
routed to `MovementPattern.isolation` ("Isolation -> target muscle") and the
reason is recorded, never given a fabricated citation — inventing provenance
while fixing a provenance defect reproduces the defect.

Source: docs/knowledge/helms-training-pyramid/chapters/
        ch03-level-2-volume-intensity-frequency.md, "Reference Tables".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .enums import MuscleGroup


class BookMuscle(str, Enum):
    """The book's own muscle vocabulary, as the ch03 chart spells it.

    Deliberately NOT `MuscleGroup`: keeping them separate is what makes the
    divergence measurable. `BOOK_TO_REPO` is the only bridge.
    """

    quads = "Quads"
    glutes = "Glutes"
    hams = "Hams"
    erectors = "Erectors"
    scapular_retractors = "Scapular retractors"
    lats = "Lats"
    biceps = "Biceps"
    triceps = "Triceps"
    rear_delts = "Rear delts"
    anterior_delts = "Anterior delts"
    middle_delts = "Middle delts"
    chest = "Chest"


class MovementPattern(str, Enum):
    """The ten rows of the ch03 chart. Closed set — the book has no others."""

    squat = "squat"
    hip_hinge = "hip_hinge"
    vertical_pull = "vertical_pull"
    vertical_push = "vertical_push"
    horizontal_pull = "horizontal_pull"
    horizontal_push = "horizontal_push"
    horizontal_hip_extension = "horizontal_hip_extension"
    pullover = "pullover"
    fly = "fly"
    isolation = "isolation"


class Fidelity(str, Enum):
    exact = "exact"
    approximate = "approximate"
    missing = "missing"


@dataclass(frozen=True)
class PatternRow:
    """One row of the chart. `book_text` is the row verbatim, for audit."""

    primary: tuple[BookMuscle, ...]
    secondary: tuple[BookMuscle, ...]
    book_text: str
    conditional: str = ""  # parentheticals the flat model cannot express

    @property
    def muscles(self) -> frozenset[BookMuscle]:
        """Every muscle the row credits.

        ch03 counts primary AND secondary 1:1 toward a muscle's weekly hard
        sets, so for conformance purposes the split does not matter — only
        set membership does.
        """
        return frozenset(self.primary) | frozenset(self.secondary)


CH03_COUNTING_CHART: dict[MovementPattern, PatternRow] = {
    MovementPattern.squat: PatternRow(
        primary=(BookMuscle.quads, BookMuscle.glutes),
        secondary=(BookMuscle.erectors,),
        book_text="Squat (all variants, leg press) | Quads, glutes | Erectors (free weights)",
        conditional="Erectors credited for free-weight variants only.",
    ),
    MovementPattern.hip_hinge: PatternRow(
        primary=(BookMuscle.glutes, BookMuscle.hams, BookMuscle.erectors),
        secondary=(BookMuscle.scapular_retractors,),
        book_text="Hip hinge (deadlifts, good morning, back ext.) | Glutes, hams, erectors | Scapular retractors",
    ),
    MovementPattern.vertical_pull: PatternRow(
        primary=(BookMuscle.lats, BookMuscle.biceps),
        secondary=(BookMuscle.rear_delts,),
        book_text="Vertical pull (chins, lat pull) | Lats, biceps | Rear delts",
    ),
    MovementPattern.vertical_push: PatternRow(
        primary=(BookMuscle.anterior_delts, BookMuscle.triceps),
        secondary=(BookMuscle.middle_delts,),
        book_text="Vertical push (OHP) | Anterior delts, triceps | Middle delts",
    ),
    MovementPattern.horizontal_pull: PatternRow(
        primary=(BookMuscle.lats, BookMuscle.scapular_retractors),
        secondary=(BookMuscle.rear_delts, BookMuscle.biceps, BookMuscle.middle_delts),
        book_text="Horizontal pull (rows) | Lats, scapular retractors | Rear delts, biceps, middle delts",
    ),
    MovementPattern.horizontal_push: PatternRow(
        primary=(BookMuscle.chest, BookMuscle.anterior_delts),
        secondary=(BookMuscle.triceps, BookMuscle.middle_delts),
        book_text=(
            "Horizontal push (bench variants) | Chest, anterior delts | "
            "Triceps (close grip/dips primary), middle delts (incline)"
        ),
        conditional=(
            "Triceps are PRIMARY for close-grip and dips; middle delts are "
            "credited on incline variants only. The flat catalog cannot "
            "express either condition — see ISSUES.md P0."
        ),
    ),
    MovementPattern.horizontal_hip_extension: PatternRow(
        primary=(BookMuscle.glutes,),
        secondary=(BookMuscle.hams,),
        book_text="Horizontal hip extension (thrust, bridge) | Glutes | Hams",
    ),
    MovementPattern.pullover: PatternRow(
        primary=(BookMuscle.lats,),
        secondary=(BookMuscle.triceps, BookMuscle.chest),
        book_text="Pullover / lat pushdown | Lats | Triceps, chest",
    ),
    MovementPattern.fly: PatternRow(
        primary=(BookMuscle.chest,),
        secondary=(BookMuscle.anterior_delts,),
        book_text="Fly | Chest | Anterior delts",
    ),
    MovementPattern.isolation: PatternRow(
        primary=(),  # resolved per-exercise: whatever the catalog calls primary
        secondary=(),
        book_text="Isolation | Target muscle | —",
        conditional=(
            "Primary is the exercise's own target, so conformance checks only "
            "that the secondary list is EMPTY."
        ),
    ),
}


# --- the bridge -------------------------------------------------------------
# Book muscle -> repo enum. `None` means the repo has no member for it at all.
BOOK_TO_REPO: dict[BookMuscle, tuple[MuscleGroup | None, Fidelity, str]] = {
    BookMuscle.quads: (MuscleGroup.quads, Fidelity.exact, ""),
    BookMuscle.glutes: (MuscleGroup.glutes, Fidelity.exact, ""),
    BookMuscle.hams: (MuscleGroup.hamstrings, Fidelity.exact, ""),
    BookMuscle.lats: (MuscleGroup.lats, Fidelity.exact, ""),
    BookMuscle.biceps: (MuscleGroup.biceps, Fidelity.exact, ""),
    BookMuscle.triceps: (MuscleGroup.triceps, Fidelity.exact, ""),
    BookMuscle.rear_delts: (MuscleGroup.rear_delt, Fidelity.exact, ""),
    BookMuscle.middle_delts: (MuscleGroup.side_delt, Fidelity.exact, ""),
    BookMuscle.scapular_retractors: (
        MuscleGroup.mid_back,
        Fidelity.approximate,
        "`mid_back` is broader than the book's scapular retractors, but nothing "
        "else in the repo covers the row.",
    ),
    BookMuscle.erectors: (
        MuscleGroup.core,
        Fidelity.approximate,
        "`core` also carries rectus-abdominis work (Crunch, Hanging Leg Raise); "
        "the book counts erectors separately from abs, so squat/hinge volume and "
        "ab volume land in one bucket.",
    ),
    BookMuscle.chest: (
        MuscleGroup.upper_chest,
        Fidelity.approximate,
        "The repo narrows the book's whole-chest category to one sub-region. "
        "Flat pressing and flyes have no correct home — ISSUES.md P0.",
    ),
    BookMuscle.anterior_delts: (
        None,
        Fidelity.missing,
        "No MuscleGroup member exists. The book makes anterior delts PRIMARY "
        "for both vertical and horizontal push; the repo records zero — "
        "ISSUES.md P0.",
    ),
}


# --- catalog assignment -----------------------------------------------------
# Canonical catalog name -> the chart row it belongs to. The catalog's
# exercise list is open and grows forever; this mapping is the only thing a
# new exercise needs, because the muscles then follow from the chart.
EXERCISE_PATTERN: dict[str, MovementPattern] = {
    "Incline Bench Press": MovementPattern.horizontal_push,
    "Decline Push-Up": MovementPattern.horizontal_push,
    "Dip": MovementPattern.horizontal_push,
    "Shoulder Press": MovementPattern.vertical_push,
    "Pull-Up": MovementPattern.vertical_pull,
    "Row": MovementPattern.horizontal_pull,
    "Straight Arm Pulldown": MovementPattern.pullover,
    "Bench Press": MovementPattern.horizontal_push,  # book: "bench variants"
    "Fly": MovementPattern.fly,
    "Lat Pulldown": MovementPattern.vertical_pull,  # book: "chins, lat pull"
    "Squat": MovementPattern.squat,
    "Leg Press": MovementPattern.squat,  # book: "Squat (all variants, leg press)"
    "Romanian Deadlift": MovementPattern.hip_hinge,
    "Hip Thrust": MovementPattern.horizontal_hip_extension,
    # Isolation — the book's catch-all row. Secondary list must be empty.
    "Lateral Raise": MovementPattern.isolation,
    "Reverse Fly": MovementPattern.isolation,  # rear-delt isolation, NOT the chest "Fly" row
    "Overhead Tricep Extension": MovementPattern.isolation,
    "Tricep Pushdown": MovementPattern.isolation,
    "Skull Crusher": MovementPattern.isolation,
    "Hammer Curl": MovementPattern.isolation,
    "Bay Curl": MovementPattern.isolation,
    "Dumbbell Curl": MovementPattern.isolation,
    "Leg Extension": MovementPattern.isolation,
    "Leg Curl": MovementPattern.isolation,
    "Cable Kickback": MovementPattern.isolation,
    "Calf Raise": MovementPattern.isolation,
    "Crunch": MovementPattern.isolation,
    "Hanging Leg Raise": MovementPattern.isolation,
    # HEURISTIC — the vendored training book never mentions face pulls (zero
    # grep hits) and no chart row covers them, so they take the catch-all
    # Isolation row with rear_delt as the target. No citation is claimed.
    "Face Pull": MovementPattern.isolation,
}


# --- accepted-as-broken -----------------------------------------------------
# Generated by `compute_deviations()`, never hand-reasoned. Shrink as
# ISSUES.md P0 is fixed; the test fails if this goes stale in either direction.
#
# Note on what is NOT here: ch03 counts primary and secondary 1:1, so an entry
# whose primary/secondary split differs from the book while covering the same
# muscles is numerically identical and does not appear. `Shoulder Press` is the
# case in point — the repo calls side_delt primary where the book calls it
# secondary, which changes nothing until `front_delt` exists.
KNOWN_DEVIATIONS: dict[str, str] = {
    # "missing side_delt" here is the chart's incline-only conditional, which
    # the flat comparison cannot express — flat bench correctly omits middle
    # delts. Listed as generated until the conditional is modelled.
    "Bench Press": "horizontal_push: no enum member for Anterior delts; missing side_delt",
    "Decline Push-Up": "horizontal_push: no enum member for Anterior delts",
    "Dip": "horizontal_push: no enum member for Anterior delts",
    "Fly": "fly: no enum member for Anterior delts",
    "Incline Bench Press": "horizontal_push: no enum member for Anterior delts",
    "Romanian Deadlift": "hip_hinge: missing core, mid_back",
    "Row": "horizontal_pull: missing side_delt",
    "Shoulder Press": "vertical_push: no enum member for Anterior delts",
    "Straight Arm Pulldown": "pullover: missing upper_chest",
}


class UnclassifiedExerciseError(LookupError):
    """A catalog entry has no `EXERCISE_PATTERN` assignment."""


def compute_deviations() -> dict[str, str]:
    """Catalog entries whose credited muscles disagree with their chart row.

    Raises `UnclassifiedExerciseError` if any catalog entry has no pattern.
    Imported lazily so this module stays importable on its own.
    """
    from .exercise_catalog import SECONDARY_OVERLAP, _ALIAS_TO_CANONICAL, lookup_key

    # A catalog entry with no pattern has no chart row to be checked against.
    # Fail loudly and name every such entry, rather than surfacing a bare
    # KeyError for whichever one happens to sort first.
    unclassified = sorted(set(SECONDARY_OVERLAP) - set(EXERCISE_PATTERN))
    if unclassified:
        raise UnclassifiedExerciseError(
            "catalog entries with no ch03 movement pattern: "
            f"{unclassified}. Add each to EXERCISE_PATTERN (models/doctrine_ch03.py) "
            "before deviations can be computed."
        )

    devs: dict[str, str] = {}
    for name in sorted(SECONDARY_OVERLAP):
        pattern = EXERCISE_PATTERN[name]
        secondaries = set(SECONDARY_OVERLAP[name])
        row = CH03_COUNTING_CHART[pattern]

        if pattern is MovementPattern.isolation:
            if secondaries:
                got = ", ".join(sorted(m.value for m in secondaries))
                devs[name] = f"isolation row requires empty secondary; has {got}"
            continue

        primary = _ALIAS_TO_CANONICAL[lookup_key(name)][1]
        repo = {primary} | secondaries

        want: set[MuscleGroup] = set()
        no_vocab: set[str] = set()
        for book_muscle in row.muscles:
            mapped, _fidelity, _why = BOOK_TO_REPO[book_muscle]
            if mapped is None:
                no_vocab.add(book_muscle.value)
            else:
                want.add(mapped)

        missing, extra = want - repo, repo - want
        if not (missing or extra or no_vocab):
            continue

        bits = []
        if no_vocab:
            bits.append("no enum member for " + ", ".join(sorted(no_vocab)))
        if missing:
            bits.append("missing " + ", ".join(sorted(m.value for m in missing)))
        if extra:
            bits.append("extra " + ", ".join(sorted(m.value for m in extra)))
        devs[name] = f"{pattern.value}: " + "; ".join(bits)
    return devs
