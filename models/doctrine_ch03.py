"""The ch03 counting chart, transcribed verbatim — conformance spec for the catalog.

`exercise_catalog.py` claims its `SECONDARY_OVERLAP` implements Helms' overlap
doctrine (Muscle & Strength Pyramid: Training ch03). That claim was added on
2026-09-09 (`b783ae3`, the commit that vendored the books) to a table written
on 2026-07-13 (`db2c8f7`), whose original comment labelled its first entry
`# Upper chest (specialization target)`. The vocabulary was never re-derived
from the book it now cites.

This module holds the book side of that comparison so the gap is *generated*
rather than eyeballed:

    CH03_COUNTING_CHART       the book's ten movement-pattern rows, verbatim
    BOOK_TO_REPO              book muscle -> MuscleGroup, with fidelity marked
    EXERCISE_PATTERN          each catalog identity -> its pattern
    DOCUMENTED_EXCEPTIONS     the chart's own conditionals, applied per exercise
                              (middle delts on incline pushes only; triceps
                              primary for close grip / dips; erectors on
                              free-weight squats only), each with its reason
    HEURISTIC_CLASSIFICATIONS classification calls the chart does not make in
                              so many words, labelled HEURISTIC with a reason
    chart_credit()            (primary, credited muscles) for an identity —
                              the ONLY source of the catalog's muscles
    KNOWN_DEVIATIONS          the accepted-as-broken state, one reason each

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
    conditional: str = ""  # parentheticals; applied per exercise via DOCUMENTED_EXCEPTIONS

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
        conditional=("Erectors credited for free-weight variants only. Applied "
                     "per exercise in DOCUMENTED_EXCEPTIONS."),
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
            "credited on incline variants only. Applied per exercise in "
            "DOCUMENTED_EXCEPTIONS."
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
    # Erectors were once folded into `core` together with ab work; the book
    # counts them separately, and so does the repo now (`core` = abs only).
    BookMuscle.erectors: (MuscleGroup.erectors, Fidelity.exact, ""),
    # The repo once had only `upper_chest`, a sub-region; the book has one
    # whole-chest category, and so does the repo now.
    BookMuscle.chest: (MuscleGroup.chest, Fidelity.exact, ""),
    BookMuscle.anterior_delts: (MuscleGroup.front_delt, Fidelity.exact, ""),
}


# --- catalog assignment -----------------------------------------------------
# Canonical catalog identity -> the chart row it belongs to. The catalog's
# exercise list is open and grows forever; this mapping is the only thing a
# new exercise needs, because the muscles then follow from the chart.
_HP, _VP, _FLY = (MovementPattern.horizontal_push, MovementPattern.vertical_push,
                  MovementPattern.fly)
_VPULL, _HPULL, _PO = (MovementPattern.vertical_pull, MovementPattern.horizontal_pull,
                       MovementPattern.pullover)
_SQ, _HH, _HHE, _ISO = (MovementPattern.squat, MovementPattern.hip_hinge,
                        MovementPattern.horizontal_hip_extension, MovementPattern.isolation)

EXERCISE_PATTERN: dict[str, MovementPattern] = {
    # book: "Horizontal push (bench variants)"
    "Bench Press": _HP, "Barbell Bench Press": _HP, "Dumbbell Bench Press": _HP,
    "Smith Bench Press": _HP, "Machine Chest Press": _HP, "Close-Grip Bench Press": _HP,
    "Incline Bench Press": _HP, "Barbell Incline Bench Press": _HP,
    "Dumbbell Incline Press": _HP, "Smith Incline Press": _HP, "Machine Incline Press": _HP,
    "Decline Push-Up": _HP, "Dip": _HP,
    # book: "Vertical push (OHP)"
    "Shoulder Press": _VP, "Barbell Overhead Press": _VP,
    "Dumbbell Shoulder Press": _VP, "Machine Shoulder Press": _VP,
    # book: "Fly"
    "Fly": _FLY, "Dumbbell Fly": _FLY, "Cable Fly": _FLY, "Machine Fly": _FLY,
    # book: "Vertical pull (chins, lat pull)"
    "Pull-Up": _VPULL, "Lat Pulldown": _VPULL, "Machine Lat Pulldown": _VPULL,
    # book: "Pullover / lat pushdown"
    "Straight Arm Pulldown": _PO,
    # book: "Horizontal pull (rows)"
    "Row": _HPULL, "Pendlay Row": _HPULL, "Cable Row": _HPULL, "Machine Row": _HPULL,
    "Reverse Row": _HPULL, "Chest-Supported Dumbbell Row": _HPULL,
    # book: "Squat (all variants, leg press)"
    "Squat": _SQ, "Bulgarian Split Squat": _SQ, "Split Squat": _SQ, "Leg Press": _SQ,
    "Bodyweight Squat": _SQ,
    # book: "Hip hinge (deadlifts, good morning, back ext.)"
    "Romanian Deadlift": _HH,
    # book: "Horizontal hip extension (thrust, bridge)"
    "Hip Thrust": _HHE, "Barbell Hip Thrust": _HHE, "Glute Bridge": _HHE,
    # Isolation — the book's catch-all row: the exercise's own target muscle only.
    # ("Reverse Fly" is rear-delt isolation, NOT the chest "Fly" row.)
    "Lateral Raise": _ISO, "Dumbbell Lateral Raise": _ISO, "Cable Lateral Raise": _ISO,
    "Machine Lateral Raise": _ISO, "Reverse Fly": _ISO, "Machine Reverse Fly": _ISO,
    "Overhead Tricep Extension": _ISO, "Tricep Pushdown": _ISO, "Skull Crusher": _ISO,
    "Dumbbell Skull Crusher": _ISO, "Hammer Curl": _ISO, "Bay Curl": _ISO, "Curl": _ISO,
    "Dumbbell Curl": _ISO, "Leg Extension": _ISO, "Leg Curl": _ISO, "Single-Leg Curl": _ISO,
    "Cable Kickback": _ISO, "Calf Raise": _ISO, "Smith Calf Raise": _ISO, "Crunch": _ISO,
    "Machine Crunch": _ISO, "Hanging Leg Raise": _ISO, "Leg Raise": _ISO,
    # HEURISTIC — the vendored training book never mentions face pulls (zero
    # grep hits) and no chart row covers them, so they take the catch-all
    # Isolation row with rear_delt as the target. No citation is claimed.
    "Face Pull": _ISO,
}

# Classification calls the chart does not make in so many words. Each is a
# reasoned reading, labelled HEURISTIC, never cited as the book's own.
HEURISTIC_CLASSIFICATIONS: dict[str, str] = {
    "Face Pull": "HEURISTIC: not in the book; routed to Isolation -> rear_delt.",
    "Decline Push-Up": (
        "HEURISTIC: a feet-elevated (\"decline\") push-up presses at the angle "
        "of an incline press, so it keeps the chart's incline middle-delt credit."
    ),
}


# --- the chart's conditionals, per exercise -------------------------------------
@dataclass(frozen=True)
class DocumentedException:
    """A chart conditional applied to one exercise — honoured, not a deviation.

    `drop` removes muscles the row lists but the conditional withholds;
    `primary` names the row's conditional primary. `reason` quotes the chart.
    """

    reason: str
    drop: frozenset[BookMuscle] = frozenset()
    primary: BookMuscle | None = None


_FLAT = ('ch03 horizontal push credits "middle delts (incline)" only — '
         "a flat press withholds them.")
_TRI = 'ch03 horizontal push: "Triceps (close grip/dips primary)".'
_MACHINE_SQUAT = ('ch03 squat credits "Erectors (free weights)" only — '
                  "a machine squat withholds them.")
_BODYWEIGHT_SQUAT = ('ch03 squat credits "Erectors (free weights)" only — '
                     "an unloaded bodyweight squat withholds them.")
_MD = frozenset({BookMuscle.middle_delts})

DOCUMENTED_EXCEPTIONS: dict[str, DocumentedException] = {
    **{name: DocumentedException(_FLAT, drop=_MD) for name in (
        "Bench Press", "Barbell Bench Press", "Dumbbell Bench Press",
        "Smith Bench Press", "Machine Chest Press")},
    "Close-Grip Bench Press": DocumentedException(
        f"{_FLAT} {_TRI}", drop=_MD, primary=BookMuscle.triceps),
    "Dip": DocumentedException(f"{_FLAT} {_TRI}", drop=_MD, primary=BookMuscle.triceps),
    "Leg Press": DocumentedException(_MACHINE_SQUAT, drop=frozenset({BookMuscle.erectors})),
    "Bodyweight Squat": DocumentedException(_BODYWEIGHT_SQUAT,
                                            drop=frozenset({BookMuscle.erectors})),
}


def _repo(book: BookMuscle) -> MuscleGroup:
    mapped, _fidelity, _why = BOOK_TO_REPO[book]
    if mapped is None:
        raise LookupError(f"{book.value} has no MuscleGroup member")
    return mapped


def chart_primaries(name: str, target: MuscleGroup | None = None) -> frozenset[MuscleGroup]:
    """Every muscle the chart lists as PRIMARY for an identity's row.

    Isolation: the target. Otherwise the row's primaries plus a conditional
    primary ("triceps (close grip/dips primary)"), minus anything a documented
    exception withholds. Used to decide which exercises speak for a muscle's
    progression (trend direction) — overlap credit counts toward volume only.
    """
    pattern = EXERCISE_PATTERN[name]
    if pattern is MovementPattern.isolation:
        if target is None:
            raise ValueError(f"isolation exercise {name!r} needs a target muscle")
        return frozenset({target})
    row = CH03_COUNTING_CHART[pattern]
    exc = DOCUMENTED_EXCEPTIONS.get(name)
    book = set(row.primary) | ({exc.primary} if exc and exc.primary else set())
    book -= exc.drop if exc else frozenset()
    return frozenset(_repo(m) for m in book)


def chart_credit(name: str, target: MuscleGroup | None = None
                 ) -> tuple[MuscleGroup, frozenset[MuscleGroup]]:
    """(primary, every credited muscle) for one catalog identity, from the chart.

    Isolation-row exercises credit exactly their `target`. Every other pattern
    credits its row's primary + secondary muscles (ch03 counts both 1:1),
    minus whatever a documented conditional withholds; the primary is the
    row's first-listed primary unless a conditional names another.
    """
    pattern = EXERCISE_PATTERN[name]
    if pattern is MovementPattern.isolation:
        if target is None:
            raise ValueError(f"isolation exercise {name!r} needs a target muscle")
        return target, frozenset({target})
    if target is not None:
        raise ValueError(f"{name!r} is {pattern.value}; only isolation takes a target")
    row = CH03_COUNTING_CHART[pattern]
    exc = DOCUMENTED_EXCEPTIONS.get(name)
    book = set(row.muscles) - (exc.drop if exc else frozenset())
    primary = exc.primary if exc and exc.primary else row.primary[0]
    return _repo(primary), frozenset(_repo(m) for m in book)


# --- accepted-as-broken -----------------------------------------------------
# Generated by `compute_deviations()`, never hand-reasoned; the test fails if
# this goes stale in either direction. Empty: every catalog identity credits
# exactly its chart row, minus the documented conditionals above. A future
# entry here must say why.
KNOWN_DEVIATIONS: dict[str, str] = {}


class UnclassifiedExerciseError(LookupError):
    """A catalog entry has no `EXERCISE_PATTERN` assignment."""


def compute_deviations() -> dict[str, str]:
    """Catalog identities whose credited muscles disagree with their chart row.

    Re-derives the expected credit straight from the chart (row muscles minus
    documented exceptions; isolation = the target alone) and compares it with
    what the catalog actually exposes (`PRIMARY` + `SECONDARY_OVERLAP`), so a
    hand edit of the catalog's derived tables cannot slip through.

    Raises `UnclassifiedExerciseError` if any catalog entry has no pattern.
    Imported lazily so this module stays importable on its own.
    """
    from .exercise_catalog import CATALOG, PRIMARY, SECONDARY_OVERLAP

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
        primary = PRIMARY[name]
        secondaries = set(SECONDARY_OVERLAP[name])

        if pattern is MovementPattern.isolation:
            bits = []
            target = CATALOG[name].target if name in CATALOG else None
            if secondaries:
                got = ", ".join(sorted(m.value for m in secondaries))
                bits.append(f"isolation row requires empty secondary; has {got}")
            if target is not None and primary is not target:
                bits.append(f"primary {primary.value}, target is {target.value}")
            if bits:
                devs[name] = "; ".join(bits)
            continue

        row = CH03_COUNTING_CHART[pattern]
        exc = DOCUMENTED_EXCEPTIONS.get(name)
        book = set(row.muscles) - (exc.drop if exc else frozenset())
        want_primary_book = exc.primary if exc and exc.primary else row.primary[0]

        want: set[MuscleGroup] = set()
        no_vocab: set[str] = set()
        for book_muscle in book:
            mapped, _fidelity, _why = BOOK_TO_REPO[book_muscle]
            if mapped is None:
                no_vocab.add(book_muscle.value)
            else:
                want.add(mapped)
        want_primary = BOOK_TO_REPO[want_primary_book][0]

        repo = {primary} | secondaries
        missing, extra = want - repo, repo - want
        bits = []
        if no_vocab:
            bits.append("no enum member for " + ", ".join(sorted(no_vocab)))
        if missing:
            bits.append("missing " + ", ".join(sorted(m.value for m in missing)))
        if extra:
            bits.append("extra " + ", ".join(sorted(m.value for m in extra)))
        if want_primary is not None and primary is not want_primary:
            bits.append(f"primary {primary.value}, chart says {want_primary.value}")
        if bits:
            devs[name] = f"{pattern.value}: " + "; ".join(bits)
    return devs
