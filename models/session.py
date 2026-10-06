"""Session-log Pydantic models — validate the raw inputs before DuckDB write.

SPec §1.2 `sessions` table + §1.3 ingestion rules.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (BaseModel, ConfigDict, Field, Strict, StrictBool, StrictInt,
                      field_validator, model_validator)

from .dates import check_plausible_date
from .enums import AnomalyCode, LoadType, MuscleGroup, PhaseType, SessionKind, WeightUnit

# A JSON number only: a bool (`true` read as 1.0) or a numeric string ("80")
# is rejected instead of coerced (P41). An int is fine where a float is meant.
StrictNum = Annotated[float, Strict()]

# The international pound, exact by definition (1959 agreement).
LB_TO_KG = 0.45359237

# Per-set plausibility bounds. None = unrecorded (bodyweight / not tracked).
# Upper bounds are generous human headroom, not physiology: they exist so
# garbage (rpe=11, sets=1e9, weight=1e308) fails at the boundary instead of
# poisoning volume sums or crashing at the DB layer (adversarial F5).
_NULL_FOR_BODYWEIGHT = ("a recorded weight must be > 0 — send null (not 0) for a bodyweight "
                        "or unrecorded set")
_PER_SET_BOUNDS: dict[str, tuple[float, float]] = {
    "reps": (1, 100),  # a recorded rep count is >= 1; null = unrecorded (P37)
    "rpe": (1, 10),    # RPE scale is 1-10; null = unrecorded (P37)
    "weight_kg": (0, 2000),
}


def _same_loads(a: list[float | None], b: list[float | None]) -> bool:
    return len(a) == len(b) and all(
        (x is None and y is None)
        or (x is not None and y is not None and math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-9))
        for x, y in zip(a, b)
    )


class ExerciseModel(BaseModel):
    """One exercise within a session. Arrays are per-set.

    `muscle_group` is optional on input — the user logs raw exercise names
    and session_logger fills it via the exercise_catalog so the user never
    has to categorize. It's set to non-null before the DB write.

    Weights: `weight_kg` is "the reading on the implement" — per hand for
    dumbbells, per side for a twin-stack cable, the stack reading on a machine,
    the total plate load on a bar (a Smith machine's own bar weight is
    unknowable and never included). `load_type` says which of those it is
    (optional; NULL = unknown). Instead of `weight_kg` a caller may send the
    numbers as read off the gym's equipment — `weight` + `unit` ('kg' | 'lb');
    weight_kg is then computed with 1 lb = 0.45359237 kg and the entered values
    are kept for read-back. Sending both forms is rejected unless they agree
    exactly (which is also what makes re-validating a converted model a no-op).
    """

    name: str
    muscle_group: MuscleGroup | None = None
    sets: StrictInt = Field(ge=1, le=50)  # per-movement headroom; a 1e9 `sets` breaks every volume sum
    reps: list[StrictNum | None] = Field(default_factory=list)
    rpe: list[StrictNum | None] = Field(default_factory=list)  # 1-10, None = unrecorded
    weight_kg: list[StrictNum | None] = Field(default_factory=list)  # per-set load; None=bodyweight/unrecorded
    weight: list[StrictNum | None] = Field(default_factory=list)  # per-set load AS ENTERED, in `unit`
    unit: WeightUnit | None = None  # unit of `weight`; never of weight_kg
    load_type: LoadType | None = None  # how the reading was taken; None = unknown
    tempo: str | None = None  # e.g. "3-1-X-1"
    form_quality: StrictInt = Field(default=5, ge=1, le=5)
    pain_flag: bool = False
    notes: str | None = None

    @field_validator("reps", "rpe", "weight_kg")
    @classmethod
    def _sane_per_set_values(cls, v: list[float | None], info) -> list[float | None]:
        lo, hi = _PER_SET_BOUNDS[info.field_name]
        for x in v:
            if x is None:
                continue
            if not math.isfinite(x):
                raise ValueError(f"{info.field_name} values must be finite numbers")
            if info.field_name == "weight_kg" and x <= 0:
                raise ValueError(f"weight_kg {_NULL_FOR_BODYWEIGHT} (got {x:g})")
            if not lo <= x <= hi:
                raise ValueError(f"{info.field_name} values must be within {lo:g}..{hi:g} (got {x:g})")
        return v

    @field_validator("weight", mode="before")
    @classmethod
    def _null_weight_is_not_given(cls, v):
        # an LLM caller often fills optional keys with null; `weight: null`
        # means "not using the weight + unit form", like `unit: null`
        return [] if v is None else v

    @field_validator("weight")
    @classmethod
    def _sane_entered_weights(cls, v: list[float | None]) -> list[float | None]:
        for x in v:
            if x is not None and not math.isfinite(x):
                raise ValueError(f"weight values must be finite numbers (got {x:g})")
            if x is not None and x <= 0:
                raise ValueError(f"weight {_NULL_FOR_BODYWEIGHT} (got {x:g})")
        return v

    @model_validator(mode="after")
    def _entered_weight_to_kg(self) -> "ExerciseModel":
        # Runs before the alignment check below (validators run in order).
        if not self.weight and self.unit is None:
            return self
        if self.unit is None:
            raise ValueError("weight needs a unit: 'kg' or 'lb'")
        if not self.weight:
            raise ValueError("unit applies to `weight`, which is empty — send the "
                             "per-set values as `weight` (weight_kg is always kg)")
        factor = LB_TO_KG if self.unit is WeightUnit.lb else 1.0
        lo, hi = _PER_SET_BOUNDS["weight_kg"]
        kg: list[float | None] = []
        for w in self.weight:
            if w is None:
                kg.append(None)
                continue
            value = w * factor
            if not lo <= value <= hi:
                raise ValueError(f"weight {w:g} {self.unit.value} is {value:g} kg, "
                                 f"outside {lo:g}..{hi:g} kg")
            kg.append(value)
        if self.weight_kg and not _same_loads(self.weight_kg, kg):
            raise ValueError("send either weight_kg or weight + unit, not both "
                             "(the two given here disagree)")
        self.weight_kg = kg
        return self

    @model_validator(mode="after")
    def _per_set_arrays_aligned(self) -> "ExerciseModel":
        # Per-set arrays must describe the same sets: a bodyweight log may omit
        # weight entirely, so fully-empty arrays are PADDED with None (None =
        # unrecorded) instead of rejected. True length conflicts (reps=[8,7],
        # rpe=[8]) are malformed and rejected before the DB write (SPEC §1.3).
        # Analytics explode these lists per set and polars crashes on unequal
        # lengths, so everything stored must be explode-safe (adversarial F4).
        lengths = {name: len(getattr(self, name))
                   for name in ("reps", "rpe", "weight_kg")}
        non_empty = {n for n in lengths.values() if n}
        if len(non_empty) > 1:
            raise ValueError(
                "reps/rpe/weight_kg are per-set arrays and must be equal length "
                f"(got reps={lengths['reps']}, rpe={lengths['rpe']}, "
                f"weight_kg={lengths['weight_kg']}); "
                "use null entries for unrecorded sets"
            )
        if non_empty and non_empty != {self.sets}:
            # a recorded array describes exactly the sets performed: sets=2 with
            # five reps and five weights would count 2 sets of volume but 5 of
            # tonnage (P36). Empty arrays are padded below, never checked.
            raise ValueError(
                f"per-set arrays must have exactly `sets` entries (sets={self.sets}, got "
                f"reps={lengths['reps']}, rpe={lengths['rpe']}, "
                f"weight_kg/weight={lengths['weight_kg']}); fix `sets` or the arrays, "
                "or leave an array empty if it was not recorded")
        target = max(non_empty) if non_empty else 0
        for name, n in lengths.items():
            if target and n == 0:
                setattr(self, name, [None] * target)
        return self


class SessionModel(BaseModel):
    """Full persisted row (post-write). Read shape from `sessions` table."""

    id: UUID | None = None
    date: date
    phase: PhaseType
    pre_recovery_score: int | None = Field(default=None, ge=0, le=100)
    exercises: list[ExerciseModel]
    post_feedback: str | None = None
    created_at: datetime | None = None


class SessionInput(BaseModel):
    """What the caller passes to session_logger.log_session().

    `phase` is optional — if omitted the current phase from phase_snapshots
    is used. This keeps user logging frictionless (they never set phase).
    `kind` defaults to a training session; see SessionKind for `habit`.
    """

    date: date
    phase: PhaseType | None = None
    pre_recovery_score: StrictInt | None = Field(default=None, ge=0, le=100)
    exercises: list[ExerciseModel]
    post_feedback: str | None = None
    kind: SessionKind = SessionKind.training

    @field_validator("date")
    @classmethod
    def _plausible_date(cls, v: date) -> date:
        return check_plausible_date(v)


class AnomalyFlag(BaseModel):
    """One anomaly raised during a log write."""

    code: AnomalyCode
    detail: str


# Keys coach_session_detail returns that are NOT inputs: derived or stored
# provenance. An amend exercise carrying one is rejected rather than silently
# ignored, so an edit made to one of them can never vanish without a word.
_READ_BACK_ONLY = {
    "raw_name": "restating the stored `name` keeps it; a new `name` is recorded as typed",
    "muscle_source": "it is recorded by the tool; restate `muscle_group` to keep it, omit it to "
                     "re-derive, set `confirm_muscle` to record the user's confirmation",
    "needs_review": "it is derived on read",
    "review_detail": "it is derived on read",
    "load_type_unknown": "it is derived on read; pass `load_type`",
    "weight_as_entered": "send `weight` + `unit` (or `weight_kg`)",
    "unit_as_entered": "send `weight` + `unit` (or `weight_kg`)",
    "entered_weight": "send it as `weight` with `unit`",
    "entered_unit": "send it as `unit` with `weight`",
}


class AmendExerciseModel(ExerciseModel):
    """An exercise in coach_session_amend: ExerciseModel's input fields plus
    the link to what it amends.

    Every amend exercise says what it is, explicitly — nothing is inferred
    from names or positions: `index` is the stored exercise it restates or
    edits (its `index` in coach_session_detail), `new: true` marks one that
    was not logged before. Exactly one of the two. Stored exercises no amend
    exercise references are removed.

    Unknown keys are rejected (a fresh log ignores them); read-back-only keys
    from coach_session_detail are rejected with a hint naming the input to
    send instead. `confirm_muscle` records `muscle_group` as the user's own
    (muscle_source caller) even where it restates the stored one.
    """

    model_config = ConfigDict(extra="forbid")

    index: StrictInt | None = Field(default=None, ge=0)
    new: StrictBool = False
    confirm_muscle: StrictBool = False

    @model_validator(mode="after")
    def _index_or_new(self) -> "AmendExerciseModel":
        if (self.index is None) == (not self.new):
            raise ValueError(
                "each amend exercise needs exactly one of `index` (the stored exercise it "
                "restates or edits, from coach_session_detail) or `new: true` (one not logged "
                f"before); got index={self.index}, new={self.new} for '{self.name}'")
        return self

    @model_validator(mode="after")
    def _confirm_needs_a_muscle(self) -> "AmendExerciseModel":
        if self.confirm_muscle and self.muscle_group is None:
            raise ValueError("confirm_muscle needs the confirmed `muscle_group`")
        return self

    @model_validator(mode="before")
    @classmethod
    def _no_read_back_keys(cls, data):
        if isinstance(data, dict):
            found = [k for k in _READ_BACK_ONLY if k in data]
            if found:
                hints = "; ".join(f"`{k}`: {_READ_BACK_ONLY[k]}" for k in found)
                raise ValueError(
                    "amend exercises take the logging fields, not the read-back shape "
                    f"({hints}). Build each exercise from index (or new: true), name, "
                    "sets, reps, rpe, weight + unit or weight_kg, load_type, tempo, "
                    "form_quality, pain_flag, notes, muscle_group (restate it to keep "
                    "it), confirm_muscle")
        return data


class SessionAmendInput(BaseModel):
    """What coach_session_amend takes: a session's new content.

    `date` and `exercises` replace the stored ones wholesale and are
    re-validated and re-canonicalized like a fresh log, except for the stored
    provenance a restatement keeps (below). Every other
    field is optional and, when omitted OR null, KEEPS its stored value —
    correcting a session's exercises must never silently clear the user's
    feedback or recovery score, or turn a habit into training. Removing a
    stored post_feedback / pre_recovery_score is an explicit act: name it in
    `clear` (it may not also be given a value).

    Exercises are AmendExerciseModel (input fields only). An exercise that
    references a stored one by `index`, as the same exercise, keeps its
    unchanged stored provenance — see
    skills.sessions._provenance_to_keep.
    """

    session_id: UUID
    date: date
    exercises: list[AmendExerciseModel]
    phase: PhaseType | None = None
    kind: SessionKind | None = None
    pre_recovery_score: StrictInt | None = Field(default=None, ge=0, le=100)
    post_feedback: str | None = None
    clear: list[Literal["post_feedback", "pre_recovery_score"]] = Field(default_factory=list)

    @field_validator("date")
    @classmethod
    def _plausible_date(cls, v: date) -> date:
        return check_plausible_date(v)

    @model_validator(mode="after")
    def _clear_or_set_not_both(self) -> "SessionAmendInput":
        indexes = [ex.index for ex in self.exercises if ex.index is not None]
        if len(indexes) != len(set(indexes)):
            raise ValueError("an `index` may be referenced by one amend exercise only")
        for name in self.clear:
            if getattr(self, name) is not None:
                raise ValueError(f"{name} is both given a value and listed in `clear`")
        return self


class RemovedExercise(BaseModel):
    """A stored exercise an amend did not carry over (amend: reported, never silent).

    `name` is None for the nameless entries some older rows hold (an entry
    that is NULL altogether reports only its index)."""

    index: int                       # its position in the session before the amend
    name: str | None = None
    raw_name: str | None = None
    muscle_group: str | None = None
    sets: int | None = None


class SessionChange(BaseModel):
    """Return of skills.sessions.amend_session() / delete_session() / restore_snapshot()."""

    action: Literal["amended", "deleted", "restored"]
    session_id: UUID
    audit_id: UUID                   # decision_log row holding the pre-change snapshot
    anomaly_flags: list[AnomalyFlag] = Field(default_factory=list)  # amend: as a fresh log
    # amend: every stored exercise the amend left out (its `index` was not
    # restated), so none disappears unreported; the full rows stay in the audit entry
    removed_exercises: list[RemovedExercise] = Field(default_factory=list)
    # amend: the session-level values now stored (kept, changed or cleared),
    # so the caller can tell the user exactly what the session looks like
    phase: str | None = None
    kind: str | None = None
    pre_recovery_score: int | None = None
    post_feedback: str | None = None
    message: str = ""


class LogConfirmation(BaseModel):
    """Return type of session_logger.log_session()."""

    session_id: UUID
    date: date
    anomaly_flags: list[AnomalyFlag] = Field(default_factory=list)
    message: str = "session logged"


class ExerciseDetail(BaseModel):
    """One logged exercise exactly as stored (read-back, coach_session_detail).

    `index` is its position in the stored session — the handle
    coach_session_amend takes to say which stored exercise an amend
    exercise restates or edits.

    Stored values are reported as-is — vocabulary fields are plain strings so
    a read never fails on what an older version stored. Provenance fields
    (raw_name, muscle_source, load_type, entered_*) are None on rows logged
    before migration 0003, meaning "unknown".
    """

    index: int | None = None                 # position in the stored session (amend handle)
    name: str | None                         # canonical identity (or the raw name if unmapped);
    #                                          None for a nameless entry some older rows hold
    raw_name: str | None = None              # what the caller typed
    muscle_group: str | None = None          # stored primary
    muscle_source: str | None = None         # catalog | keyword | caller | unclassified
    needs_review: bool = False               # derived from the stored muscle_source
    review_detail: str | None = None         # why, when needs_review
    sets: int | None = None
    reps: list[float | None] = Field(default_factory=list)
    rpe: list[float | None] = Field(default_factory=list)
    weight_kg: list[float | None] = Field(default_factory=list)
    load_type: str | None = None             # per_hand | per_side | total | machine_stack | bodyweight
    load_type_unknown: bool = True           # no load_type stored: tonnage counts it as read
    weight_as_entered: list[float | None] = Field(default_factory=list)  # the user's own numbers
    unit_as_entered: str = "kg"              # their unit ('kg' for weight_kg callers)
    entered_weight: list[float | None] | None = None  # stored weight+unit form, if used
    entered_unit: str | None = None
    tempo: str | None = None
    form_quality: int | None = None
    pain_flag: bool | None = None
    notes: str | None = None


class SessionDetail(BaseModel):
    """One logged session in full — return of skills.sessions.get_session_detail()."""

    id: UUID
    date: date
    phase: str | None = None
    kind: str = "training"
    pre_recovery_score: int | None = None
    post_feedback: str | None = None
    created_at: datetime | None = None
    exercises: list[ExerciseDetail] = Field(default_factory=list)
    needs_review_count: int = 0
