"""Controlled vocabularies — validated at the Pydantic boundary.

v2: the DB stores phase/location as VARCHAR (migration 0002) and the
per-exercise muscle_group as VARCHAR too (migration 0003); these enums are the
single source of the allowed vocabulary. Extending a vocabulary requires no
migration — add a value here and the validation layer enforces it.

Phase names follow Helms' block-periodization vocabulary (Muscle & Strength
Pyramid: Training ch04) so program state maps 1:1 onto the doctrine.
"""

from enum import Enum


class MuscleGroup(str, Enum):
    """Muscles that hard sets are credited to.

    Aligned with the Training ch03 counting chart's own vocabulary (the chart
    models/doctrine_ch03.py transcribes; BOOK_TO_REPO there is the bridge).
    Repo names that differ from the book's are noted per member; members the
    chart does not have are labelled HEURISTIC with the reason.
    """

    side_delt = "side_delt"    # book: "Middle delts"
    rear_delt = "rear_delt"    # book: "Rear delts"
    front_delt = "front_delt"  # book: "Anterior delts" (primary for vertical AND horizontal push)
    # book: "Chest" — one category; incline vs flat is carried by the exercise
    # name, never by the muscle. Replaces the former `upper_chest` (see _missing_).
    chest = "chest"
    mid_back = "mid_back"      # book: "Scapular retractors"
    lats = "lats"
    biceps = "biceps"
    triceps = "triceps"
    quads = "quads"
    hamstrings = "hamstrings"  # book: "Hams"
    glutes = "glutes"
    erectors = "erectors"      # book: "Erectors" (squat / hip-hinge credit)
    # HEURISTIC — not a ch03 chart muscle: abdominal work only (crunches, leg
    # raises). Erector credit is `erectors`, never `core`.
    core = "core"
    # HEURISTIC — not in the ch03 chart, which has no calf movement pattern;
    # calf raises fit its "Isolation -> target muscle" row.
    calves = "calves"
    # HEURISTIC — not in the ch03 chart; kept so a caller can still name it.
    # No catalog exercise credits it.
    serratus = "serratus"
    # Sentinel, not a muscle: an exercise the system could not map. Its sets
    # stay loggable but are credited to no muscle's volume or trend.
    unclassified = "unclassified"

    @classmethod
    def _missing_(cls, value: object) -> "MuscleGroup | None":
        # `upper_chest` was replaced by `chest` (migration 0003 remapped every
        # stored value). Accepting the old spelling keeps every previously valid
        # call — log input, trend query, stored profile JSON — working with the
        # same meaning.
        if value == "upper_chest":
            return cls.chest
        return None


class LoadType(str, Enum):
    """How a logged weight was measured on the implement (weight_kg is "the
    reading on the implement"). per_hand / per_side mean BOTH limbs work with
    that load each; a single-arm movement uses `total`."""

    per_hand = "per_hand"            # dumbbells: one hand's load
    per_side = "per_side"            # twin-stack cable: one side's stack
    total = "total"                  # barbell / plate-loaded: whole external load
    machine_stack = "machine_stack"  # selectorized machine / single cable stack reading
    bodyweight = "bodyweight"        # no external load recorded


class WeightUnit(str, Enum):
    kg = "kg"
    lb = "lb"


class SessionKind(str, Enum):
    """What a logged session is (sessions.kind, migration 0003).

    A `habit` is a standing daily item done outside training (e.g. 60
    bodyweight squats every morning): its sets are sets and count toward
    volume, but it is not a training session — it never feeds recovery's
    training-load inputs, the session-gap / staleness signal, deload block
    state or the phase resolver. Pain logged during a habit DOES count
    toward recovery (pain is about the body, not the training load).
    """

    training = "training"
    habit = "habit"


class MuscleSource(str, Enum):
    """How a logged exercise's muscle_group was decided (exercises.muscle_source)."""

    catalog = "catalog"            # the name is a catalog identity / alias
    keyword = "keyword"            # unknown name; primary guessed from a keyword
    caller = "caller"              # the caller set muscle_group explicitly
    unclassified = "unclassified"  # unknown name, no keyword: credited to no muscle


class PhaseType(str, Enum):
    maintenance = "maintenance"            # off-season / no specific push
    reconditioning = "reconditioning"      # return from layoff or into base work
    accumulation = "accumulation"          # volume block (sets up, RPE 5–8)
    intensification = "intensification"    # load/RPE climb (sets down)
    realization = "realization"            # taper / test / display fitness
    deload = "deload"                      # planned low-stress week
    cut = "cut"                            # fat-loss phase (training + diet)
    lean_bulk = "lean_bulk"                # gaining phase (training + diet)


class PainLocation(str, Enum):
    left_elbow = "left_elbow"
    right_elbow = "right_elbow"
    left_knee = "left_knee"
    right_knee = "right_knee"
    left_shoulder = "left_shoulder"
    right_shoulder = "right_shoulder"
    left_hip = "left_hip"
    right_hip = "right_hip"
    lower_back = "lower_back"
    none = "none"  # sentinel for "pain reported, no specific location" — not a body part


class InjuryState(str, Enum):
    active = "active"
    resolving = "resolving"
    resolved = "resolved"
    chronic_baseline = "chronic_baseline"


class TrendDirection(str, Enum):
    """est-1RM / hard-set trend across the window halves (trend_analysis)."""

    up = "up"
    down = "down"
    plateau = "plateau"
    unknown = "unknown"


class AnomalyCode(str, Enum):
    """Exactly the codes session_logger emits when writing a session (and,
    for `amend_not_applied`, the amend tool)."""

    pain_flag = "pain_flag"
    form_quality_low = "form_quality_low"
    needs_review = "needs_review"
    amend_not_applied = "amend_not_applied"  # a value copied from a replaced exercise


class DecisionEventType(str, Enum):
    """Controlled vocabulary for the decision_log audit trail (SPEC §5)."""

    plan_modification = "plan_modification"
    anomaly = "anomaly"
    goal_change = "goal_change"
    session_amend = "session_amend"    # payload: the complete pre-change row
    session_delete = "session_delete"  # payload: the complete deleted row
    session_restore = "session_restore"  # payload: the row it overwrote (before), the restored row (after)
