"""Session-log Pydantic models — validate the raw inputs before DuckDB write.

SPec §1.2 `sessions` table + §1.3 ingestion rules.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (BaseModel, ConfigDict, Field, PrivateAttr, Strict, StrictBool, StrictInt,
                      ValidationError, field_validator, model_validator)

from .dates import check_plausible_date
from .text import clean_label, clean_name, clean_text
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

    @field_validator("reps", "rpe", "weight_kg", mode="before")
    @classmethod
    def _null_array_is_not_recorded(cls, v):
        # an explicit null means "not recorded", exactly like omitting the key (P58)
        return [] if v is None else v

    @field_validator("form_quality", mode="before")
    @classmethod
    def _null_form_quality_is_the_default(cls, v):
        return 5 if v is None else v

    @field_validator("pain_flag", mode="before")
    @classmethod
    def _null_pain_flag_is_the_default(cls, v):
        return False if v is None else v

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return clean_name(v)

    @field_validator("notes")
    @classmethod
    def _clean_notes(cls, v: str | None, info) -> str | None:
        return clean_text(v, info.field_name)

    @field_validator("tempo")
    @classmethod
    def _clean_tempo(cls, v: str | None, info) -> str | None:
        return clean_text(v, info.field_name, allowed=())  # single-line: no control chars

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
    label: str | None = None

    @field_validator("label")
    @classmethod
    def _clean_label(cls, v: str | None) -> str | None:
        return clean_label(v)

    @field_validator("date")
    @classmethod
    def _plausible_date(cls, v: date) -> date:
        return check_plausible_date(v)

    @field_validator("exercises")
    @classmethod
    def _at_least_one_exercise(cls, v: list[ExerciseModel]) -> list[ExerciseModel]:
        if not v:
            raise ValueError("a session needs at least one exercise")
        return v

    @field_validator("post_feedback")
    @classmethod
    def _clean_feedback(cls, v: str | None) -> str | None:
        return clean_text(v, "post_feedback")


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


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _per_set_or_none(values, sets) -> list | None:
    """A submitted per-set array as a comparable list (None / empty = unrecorded
    for every set, padded to `sets` like the stored side); None if malformed."""
    if values is None or values == []:
        ok = isinstance(sets, int) and not isinstance(sets, bool) and sets > 0
        return [None] * sets if ok else []
    if not isinstance(values, list) or not all(x is None or _num(x) for x in values):
        return None
    return values


def _same_arrays(a: list | None, b: list | None) -> bool:
    return a is not None and b is not None and len(a) == len(b) and all(
        (x is None and y is None)
        or (x is not None and y is not None and math.isclose(x, y, rel_tol=1e-6, abs_tol=1e-9))
        for x, y in zip(a, b))


def stored_if_restated_unchanged(data, stored: list | None) -> dict | None:
    """The stored exercise an amend exercise (raw input dict) restates UNCHANGED,
    else None (P58). Unchanged = it references a stored exercise by `index` and
    every logging field it sends equals what is stored: name exactly, muscle_group
    (omitted counts as a change: it re-derives), sets, the per-set arrays (reps /
    rpe / weight_kg, or weight + unit in kg terms; omitted or empty = unrecorded),
    tempo, notes; load_type omitted or equal; form_quality / pain_flag an explicit
    null (= keep) or equal. A key the model does not accept, `new`, or
    `confirm_muscle` is never a plain restatement."""
    allowed = set(AmendExerciseModel.model_fields)
    if not isinstance(data, dict) or set(data) - allowed:
        return None
    idx = data.get("index")
    if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(stored or []):
        return None
    if data.get("new") or data.get("confirm_muscle"):
        return None
    s = (stored or [])[idx]
    if not isinstance(s, dict):
        return None
    sets = data.get("sets")
    if (s.get("name") is None or data.get("name") != s.get("name")
            or data.get("muscle_group") != s.get("muscle_group")
            or sets != s.get("sets") or isinstance(sets, bool)
            or data.get("tempo") != s.get("tempo") or data.get("notes") != s.get("notes")):
        return None
    for key in ("reps", "rpe"):
        stored_vals = list(s.get(key) or []) or _per_set_or_none([], sets)
        if not _same_arrays(_per_set_or_none(data.get(key), sets), stored_vals):
            return None
    kg = data.get("weight_kg")
    if data.get("weight"):
        unit, w = data.get("unit"), data["weight"]
        if unit not in ("kg", "lb") or not isinstance(w, list) or not all(x is None or _num(x) for x in w):
            return None
        f = LB_TO_KG if unit == "lb" else 1.0
        kg = [None if x is None else x * f for x in w]
        if data.get("weight_kg") and not _same_arrays(kg, _per_set_or_none(data["weight_kg"], sets)):
            return None
    elif data.get("unit") is not None:
        return None
    if not _same_arrays(_per_set_or_none(kg, sets),
                        list(s.get("weight_kg") or []) or _per_set_or_none([], sets)):
        return None
    if data.get("load_type") is not None and data["load_type"] != s.get("load_type"):
        return None
    for key, default in (("form_quality", 5), ("pain_flag", False)):
        if key in data and data[key] is None:
            continue  # explicit null: keep what is stored
        v = data.get(key, default)
        if type(v) is not type(s.get(key)) or v != s.get(key):
            return None
    return s


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
    # set by the wrap validator below only: this exercise restates a stored one
    # unchanged whose values today's input rules reject, so it is kept as stored
    _keep_stored: bool = PrivateAttr(default=False)

    @property
    def keep_stored(self) -> bool:
        return self._keep_stored

    @model_validator(mode="wrap")
    @classmethod
    def _restated_unchanged(cls, data, handler, info):
        """P58: an exercise restated unchanged is not re-checked against the
        input rules. Needs the stored exercises in the validation context
        (`context={"stored_exercises": [...]}`); without it every exercise is
        validated in full. A valid restatement flows as before (null
        form_quality / pain_flag keep the stored values); one the rules reject
        (legacy values), or whose stored form_quality / pain_flag is null,
        is kept verbatim."""
        stored = (info.context or {}).get("stored_exercises")
        s = stored_if_restated_unchanged(data, stored) if stored is not None else None
        if s is None:
            return handler(data)
        if s.get("form_quality") is not None and s.get("pain_flag") is not None:
            data = {**data}
            for key in ("form_quality", "pain_flag"):
                if key in data and data[key] is None:
                    data[key] = s[key]
            try:
                return handler(data)
            except ValidationError:
                pass
        obj = cls.model_construct(index=data["index"], name=s["name"], sets=s.get("sets"))
        obj._keep_stored = True
        return obj

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
    stored post_feedback / pre_recovery_score / label is an explicit act: name it in
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
    label: str | None = None
    clear: list[Literal["post_feedback", "pre_recovery_score", "label"]] = Field(
        default_factory=list)

    @field_validator("label")
    @classmethod
    def _clean_label(cls, v: str | None) -> str | None:
        return clean_label(v)

    @field_validator("date")
    @classmethod
    def _plausible_date(cls, v: date, info) -> date:
        stored = (info.context or {}).get("stored_date")
        if stored is not None and v == stored:
            return v  # restating the stored date unchanged is not re-checked (P58)
        return check_plausible_date(v)

    @field_validator("exercises")
    @classmethod
    def _amend_needs_exercises(cls, v: list[AmendExerciseModel]) -> list[AmendExerciseModel]:
        if not v:
            raise ValueError(
                "exercises is empty: an amend replaces the session's exercises, so an empty "
                "list would silently delete them all. To remove the session use "
                "coach_session_delete; otherwise restate the exercises to keep")
        return v

    @field_validator("post_feedback")
    @classmethod
    def _clean_feedback(cls, v: str | None) -> str | None:
        return clean_text(v, "post_feedback")

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
    audit_id: UUID | None            # decision_log row holding the pre-change snapshot
    #                                  (None when an amend changed nothing: no entry is written)
    changed: bool = True             # amend: False when the session would be stored byte-identically
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
    label: str | None = None
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
    # unit_as_entered: their unit ('kg' for weight_kg callers; falls back to kg for rows
    # written before units were recorded); null when no weight was entered at all.
    # weight_as_entered / unit_as_entered are this EFFECTIVE read-back; entered_weight /
    # entered_unit below are the RAW stored weight + unit form (null on those older rows).
    unit_as_entered: str | None = None
    entered_weight: list[float | None] | None = None  # raw stored weight+unit form, if used
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
    label: str | None = None
    created_at: datetime | None = None
    exercises: list[ExerciseDetail] = Field(default_factory=list)
    needs_review_count: int = 0


class SessionTemplate(BaseModel):
    """Return of skills.session_templates.session_template(): a stored session as
    a ready-to-log payload (UX3 "same as last time"). `exercises` holds EXACT
    coach_log_session exercise inputs (plain dicts on purpose - they are re-sent
    verbatim; a documented exception to "skills return models"), carrying only
    what repeats: name, sets, reps, weight + unit as entered, load_type, tempo,
    and muscle_group when the caller had set it. Per-day observations (rpe,
    pain_flag, notes, form_quality, post_feedback, pre_recovery_score) are never
    copied. `skipped` names stored entries that cannot be re-logged (a nameless
    older entry)."""

    source_session_id: UUID
    source_date: date
    label: str | None = None
    kind: str = "training"
    exercises: list[dict] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class LabelSummary(BaseModel):
    """One label in use (session_labels): the stored spelling of its most recent
    session, kind of that session, how many sessions carry it (normalized), the
    most recent date, and whole days since then (0 = logged today)."""

    label: str
    kind: str
    count: int
    last_date: date
    days_without_entry: int
