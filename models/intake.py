"""Intake-assessment models — the standardized "bucket list" of everything
the coach needs before programming decisions, and the report of what the
database already holds.

The checklist is DATA (INTAKE_CHECKLIST), not prompt prose: one entry per
intake factor, each citing its vendored-book source or carrying an explicit
HEURISTIC label (unsourced doctrine was deliberately removed from this repo —
a heuristic field must say so, see docs/IDEAS.md #1). skills.intake.assess_
intake() scans stored state against this list; the coach cites what's
present (asking "still accurate?"), asks only for what's missing, in
checklist order, and applies the soft per-domain gates (COACH_PROMPT
"Standardized intake assessment").

Excluded from the bucket list on purpose (docs/adr/0001-...md and
docs/adr/0002-omitted-and-derived-intake-fields.md): sleep as a static field
(both books treat recovery questions as recurring check-ins), food
allergies/dislikes (the nutrition book is anti-preference-exclusion),
budget/cooking skill, clinical screening, motivation scoring — and, after
first-use feedback: meals (frequency is book-neutral, Nutrition ch05),
social support (too abstract, effect unpredictable), priority muscles
(derived from goals + observed weak points, never asked). They stay
answerable as ordinary coaching questions.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GateDomain(str, Enum):
    """Which decision domain a field gates. `both` = goal-level context the
    training AND nutrition books both consume (e.g. the goal itself)."""

    training = "training"
    nutrition = "nutrition"
    both = "both"


class GateCondition(str, Enum):
    """When a missing `blocks_gate` field actually holds its gate open.
    Declared on the field (data), evaluated by skills.intake."""

    always = "always"
    unless_male = "unless_male"    # sex-specific question: skipped only when sex is male
    when_cutting = "when_cutting"  # only when a goal is fat_loss or physique_target ripped


class FieldStatus(str, Enum):
    collected = "collected"  # a value exists in storage (caveat: confirm it's still true)
    missing = "missing"      # nothing stored — the coach must ask


class IntakeOption(BaseModel):
    """One answer the user can pick for an enum/bool-like field, with its
    consequence stated up front — a choice must never be made blind (an option
    that silently switched on cut rules once was picked from a translated label)."""

    value: str    # the real enum value (or "true"/"false" for bool fields)
    means: str    # plain-language meaning, one short sentence
    effect: str   # what choosing it changes in coaching, one short sentence
    source: str   # vendored-book citation, or "HEURISTIC — <why>"


# Guided-round titles (training intake). The rounds themselves are declared on
# the checklist fields (`IntakeField.round`); this only names them.
INTAKE_ROUND_TITLES: dict[int, str] = {
    1: "goal & starting point",
    2: "your week",
    3: "calibration & safety",
}


class IntakeField(BaseModel):
    """One bucket-list entry: what to know, where it lives, what it gates."""

    name: str            # canonical checklist key (snake_case)
    source: str          # book citation, or "HEURISTIC — ..." (never neither)
    storage: str         # "profile.<attr>" or "injury_status"
    gates: GateDomain
    blocks_gate: bool = True  # False = reported when missing, but never holds a gate open
    # When a missing blocking field really blocks (see GateCondition). A field
    # whose condition is not met is reported missing but does not block.
    gate_condition: GateCondition = GateCondition.always
    # list-typed profile fields only: True = empty list means "never asked"
    # (strict presence — goals, weekly_availability); False = empty is a valid
    # "nothing applies" answer. Declared here so the resolver carries no
    # per-name special cases.
    empty_means_missing: bool = False
    note: str = ""       # how the coach uses it / how to collect it
    # Guided-round number (1-3) in which the coach asks this field; None = not
    # asked in the guided rounds (emerges later, or belongs to the parked
    # nutrition intake).
    round: int | None = None
    question: str = ""   # one plain-language question (English; the coach translates)
    # choices whose pick changes what the coach does; each states its effect
    options: list[IntakeOption] = Field(default_factory=list)
    # profile sub-field the options choose between, when it is not the field
    # itself (goals -> physique_target); "" = the field itself
    options_key: str = ""


class FieldReport(IntakeField):
    """Outcome of scanning one IntakeField against stored state.

    Subclasses IntakeField so the report carries the field's own name/source/
    gates/blocks_gate verbatim (flat JSON for the LLM surface) with no
    field-by-field copying to drift.
    """

    status: FieldStatus
    value: Any = None    # JSON-safe stored value; always None when missing
    # True when this field, being missing, holds its gate open right now
    # (blocks_gate and its gate_condition met for the stored profile)
    blocks_now: bool = False


class DomainProgress(BaseModel):
    """done/total over the fields that gate a domain right now: blocking
    fields whose gate_condition applies to the stored profile."""

    done: int = 0
    total: int = 0


class NextRound(BaseModel):
    """The guided round to run next, with the fields to ask in it."""

    round: int
    title: str = ""
    fields: list[str] = Field(default_factory=list)


class FieldSummary(BaseModel):
    """The compact per-field view (coach_intake_status default): what the scan
    found, without the question/options/source/note prose."""

    name: str
    status: FieldStatus
    value: Any = None
    blocks_now: bool = False
    round: int | None = None
    gates: GateDomain


class FieldDetail(BaseModel):
    """A field's full definition, as the coach needs it to ask the question."""

    name: str
    source: str
    question: str = ""
    options: list[IntakeOption] = Field(default_factory=list)
    note: str = ""


class DetailedNextRound(NextRound):
    """NextRound plus the full definition of exactly the fields it lists."""

    field_details: list[FieldDetail] = Field(default_factory=list)


class IntakeReport(BaseModel):
    """Return of intake.assess_intake() — the whole checklist, scanned.

    `*_ready` means no BLOCKING field gating that domain is missing (soft
    gates: the coach withholds domain plans until ready, but may produce a
    provisional plan with explicit limitations if the user insists —
    COACH_PROMPT). `weeks_since_last_session` is staleness context for the
    confirm-present step (threshold: skills.snapshot.REASSESSMENT_GAP_WEEKS,
    a heuristic).
    """

    fields: list[FieldReport] = Field(default_factory=list)
    weeks_since_last_session: float | None = None
    training_ready: bool = False
    nutrition_ready: bool = False
    missing: list[str] = Field(default_factory=list)
    # convenience view of `missing` grouped by domain ("training"/"nutrition");
    # redundant with fields/missing/*_ready by design — the LLM surface cites
    # whichever shape is cheapest per turn
    missing_by_gate: dict[str, list[str]] = Field(default_factory=dict)
    # "training"/"nutrition" -> done/total over the currently applicable
    # blocking fields (a cutting goal adds bodyfat_pct to nutrition's total)
    progress: dict[str, DomainProgress] = Field(default_factory=dict)
    # number of guided training rounds, derived from the checklist
    rounds_total: int = 0
    # Lowest guided round that is not done, else None. A round is done when
    # every field in it that is BLOCKING NOW is collected; non-blocking fields
    # (injuries: no rows = nothing reported) are listed in `fields` only while
    # the round is still pending, so a user with no injuries is not stuck on
    # round 3 forever.
    next_round: NextRound | None = None


# The bucket list — single source of truth for the standardized intake.
# Order mirrors the books' own priority hierarchies (Training ch01: adherence
# → volume/intensity/frequency → …; Nutrition ch01: energy → macros → …):
# goals first, then training-gated factors, then nutrition-gated factors.
INTAKE_CHECKLIST: list[IntakeField] = [
    IntakeField(
        name="goals", source="Training ch02 (deadlines), ch08 (goal column); Nutrition ch02 (rates by goal)",
        storage="profile.goals", gates=GateDomain.both, empty_means_missing=True,
        note=("kind + physique_target + target_muscles + deadline; deadline drives taper and diet timing. "
              "A vision stated vaguely is recorded as-is (notes/metric) — it is provisional and sharpens "
              "as understanding grows. Empty is NOT an answer: 'no specific goal' is kind=general_fitness"),
        round=1,
        question="What is your main goal (build muscle, get stronger, lose fat, general fitness, ...) and what physique do you picture: ripped, athletic or bulky?",
        options_key="physique_target",
        options=[
            IntakeOption(value="ripped",
                         means="A lean, low body-fat look.",
                         effect="Switches ON cut rules: body-fat % becomes required before nutrition is ready, fat loss runs at 0.5-1.0% of bodyweight per week, and in a long or aggressive cut training steps down a volume tier and deloads automatically.",
                         source="Nutrition ch02 (lose 0.5-1.0%/week), Nutrition ch05 (refeed gate by body-fat %); Training ch08 (training in a cut)"),
            IntakeOption(value="athletic",
                         means="A balanced mix of muscle and condition.",
                         effect="No cut or gain rules switch on and body-fat % is not required.",
                         source="HEURISTIC - the books give cut and gain rates but define no athletic target; no extra rule is attached to it"),
            IntakeOption(value="bulky",
                         means="A mass-focused look.",
                         effect="The diet side uses the monthly weight-gain rate for your training age; no cut rules apply.",
                         source="Nutrition ch02 (rate of weight gain by training age)"),
        ],
    ),
    IntakeField(
        name="training_age", source="Training ch04 (classify by RATE OF PROGRESS — workout-to-workout / week-to-week / month-to-month — not years lifting)",
        storage="profile.training_age", gates=GateDomain.training,
        note="sets volume tier, frequency, progression model, gain-rate bracket",
        round=1,
        question="When you train, how fast do you still progress: every workout, every week, or only over months?",
        options=[
            IntakeOption(value="novice",
                         means="You can add load or reps almost every workout.",
                         effect="Linear progression (add load each session) and the lowest weekly-set tier, 10-12 sets per muscle.",
                         source="Training ch04 (single linear progression), Training ch08 (volume by training age)"),
            IntakeOption(value="intermediate",
                         means="Progress shows week to week, not every session.",
                         effect="Wave-style periodization (double progression on isolation lifts) and the middle tier, 13-15 sets per muscle.",
                         source="Training ch04 (linear-periodized wave, double progression), Training ch08 (volume by training age)"),
            IntakeOption(value="advanced",
                         means="Progress is visible only over months.",
                         effect="Planned, block-periodized progression and the highest tier, 16-20 sets per muscle.",
                         source="Training ch04 (planned, tested progression), Training ch08 (volume by training age)"),
        ],
    ),
    IntakeField(
        name="days_per_week", source="Training ch02 ('start with what you can do'), ch08 (2–6 days; 3–5 for 90% of people)",
        storage="profile.days_per_week", gates=GateDomain.training,
        note="committed training days; the WHEN comes from weekly_availability",
        round=1,
        question="How many days per week can you realistically train?",
    ),
    IntakeField(
        name="weekly_availability", source="Training ch02 ('start with what you can do' — match the program to the actual week) — window structure HEURISTIC",
        storage="profile.weekly_availability", gates=GateDomain.training, blocks_gate=False,
        empty_means_missing=True,
        note=("time windows per weekday; venue captures a closing gym. Vague answers are fine — "
              "the coach formats them into windows; plan the week around them"),
    ),
    IntakeField(
        name="life_stress", source="Training ch02 (life stress and training stress are one cumulative bucket)",
        storage="profile.life_stress", gates=GateDomain.training,
        note=("work/sleep/family load; high stress ⇒ the training side must drop. A rolling "
              "recent-overall impression — re-confirm at check-ins, expected to move"),
        round=2,
        question="Outside the gym, how stressful are work, sleep and family right now: low, moderate or high?",
        options=[
            IntakeOption(value="low",
                         means="Life is calm.",
                         effect="No life-side cut to training stress.",
                         source="HEURISTIC - the book states the principle (life stress counts against recovery) but gives no numeric bands"),
            IntakeOption(value="moderate",
                         means="Some pressure, still manageable.",
                         effect="No cut up front; training stress is reduced if recovery signals slip.",
                         source="HEURISTIC - the book states the principle (life stress counts against recovery) but gives no numeric bands"),
            IntakeOption(value="high",
                         means="Heavy load from work, sleep loss or family.",
                         effect="The training side is turned down to fit the total stress budget.",
                         source="Training ch02 (life stress and training stress are one cumulative bucket)"),
        ],
    ),
    IntakeField(
        name="injuries", source="Training ch02 (pain is information), ch05 (pain caps volume; substitutions)",
        storage="injury_status", gates=GateDomain.training, blocks_gate=False,
        note=("rows in injury_status; seed via coach_injuries_seed on any report. "
              "No rows = no reported injuries, so this never holds the gate open — "
              "persona rule 2 (always check injuries) guarantees the ask. "
              "Re-confirm periodically: shelf-life tracking is deferred (docs/IDEAS.md #1)."),
        round=3,
        question="Any current or past injuries, or movements that hurt?",
    ),
    IntakeField(
        name="exercise_likes", source="Training ch05 (lifters who choose exercises out-gain fixed selection)",
        storage="profile.liked_exercises", gates=GateDomain.training, blocks_gate=False,
        note=("enjoyment drives adherence (ch02); bounded by proficiency. Emerges over the "
              "first blocks — the coach proposes, records reactions here"),
    ),
    IntakeField(
        name="exercise_dislikes", source="Training ch02 (enjoyment drives effort), ch05 (preference is a sanctioned selection input)",
        storage="profile.disliked_exercises", gates=GateDomain.training, blocks_gate=False,
        note=("hated exercises get swapped for pain-free comparable patterns. Emerges through "
              "training — not knowable at intake, never forced"),
    ),
    IntakeField(
        name="concurrent_sports", source="Training ch02 (interference effect; one activity takes priority)",
        storage="profile.concurrent_sports", gates=GateDomain.training,
        note=("other sports/cardio: ordering mitigations + priority principle apply. "
              "Never asked = None (missing); asked, none = [] (collected)"),
        round=2,
        question="Do you do any other sport or regular cardio (\"none\" is a valid answer)?",
    ),
    IntakeField(
        name="rpe_calibrated", source="Training ch08 (novices track RPE without programming by it until calibrated)",
        storage="profile.rpe_calibrated", gates=GateDomain.training,
        note="false/None ⇒ loads prescribed by feel + repetition, not RPE targets",
        round=3,
        question="Can you reliably tell how many reps you had left in a set (RPE)?",
        options=[
            IntakeOption(value="true",
                         means="You can judge how close to failure a set was.",
                         effect="Loads can be prescribed as RPE ranges.",
                         source="Training ch08 (load by %1RM with an RPE range)"),
            IntakeOption(value="false",
                         means="You are still learning to judge it.",
                         effect="RPE is tracked but loads are prescribed by feel and repetition until calibrated.",
                         source="Training ch08 (novices track RPE without programming by it until calibrated)"),
        ],
    ),
    IntakeField(
        name="has_tested_maxes", source="Training ch08 ('no tested 1RM on a lift → RPE alone'), ch09 (test a 3–5RM with spotters)",
        storage="profile.has_tested_maxes", gates=GateDomain.training,
        note="whether real percentages can be computed or RPE-only applies",
        round=3,
        question="Have you tested a real 1RM (or 3-5RM) on your main lifts?",
        options=[
            IntakeOption(value="true",
                         means="You know a tested max on the lift.",
                         effect="Real %1RM loads can be computed.",
                         source="Training ch08 (program %1RM with an RPE range)"),
            IntakeOption(value="false",
                         means="No tested max.",
                         effect="Loads are set by RPE alone.",
                         source="Training ch08 (no tested 1RM on a lift: RPE alone)"),
        ],
    ),
    IntakeField(
        name="session_length_min", source="HEURISTIC — the books DERIVE session length from frequency (Training ch08), they never ask it",
        storage="profile.session_length_min", gates=GateDomain.training,
        note="kept because real schedules constrain session count × length",
        round=1,
        question="About how many minutes can one session last?",
    ),
    IntakeField(
        name="equipment_access", source="HEURISTIC — not an intake item in the books; implied by machine-exercise classes (Training ch08) and substitution rules (ch09)",
        storage="profile.equipment_access", gates=GateDomain.training,
        note="None until the user answers — never assume full_gym",
        round=2,
        question="What equipment do you have: a full gym, a home setup, or minimal equipment?",
        options=[
            IntakeOption(value="full_gym",
                         means="Commercial gym with barbells, machines and cables.",
                         effect="Any catalog exercise can be programmed.",
                         source="HEURISTIC - equipment is not an intake item in the books"),
            IntakeOption(value="home",
                         means="A home setup with limited implements.",
                         effect="Exercises are limited to what you own; substitutions keep the movement pattern.",
                         source="Training ch09 (substitutions preserve movement pattern)"),
            IntakeOption(value="minimal",
                         means="Bodyweight or very few implements.",
                         effect="Selection is narrowed hard; substitutions keep the movement pattern.",
                         source="Training ch09 (substitutions preserve movement pattern)"),
        ],
    ),
    IntakeField(
        name="sex", source="Nutrition ch03 (female fiber floor 20g vs 25g), ch05 (refeed gate pairing with bodyfat)",
        storage="profile.sex", gates=GateDomain.nutrition,
        note="gates refeed thresholds and fiber floor",
    ),
    IntakeField(
        name="age_years", source="Nutrition ch03 (insulin-resistance predictor; defaults assume under ~60)",
        storage="profile.age_years", gates=GateDomain.nutrition,
        note="macro-branch gate, not a volume prescription",
    ),
    IntakeField(
        name="bodyweight_kg", source="Nutrition ch02 (BW×10 maintenance, protein g/lb, %-BW rates), ch03, ch04 (fluids)",
        storage="profile.bodyweight_kg", gates=GateDomain.nutrition,
        note="the single most load-bearing nutrition number; prefer the tracked series (coach_bodyweight_log; snapshot body_weight_kg = 7-day morning_fasted mean) when available",
    ),
    IntakeField(
        name="bodyfat_pct", source="Nutrition ch05 (refeed gate: ~12% M / ~20% F)",
        storage="profile.bodyfat_pct", gates=GateDomain.nutrition,
        gate_condition=GateCondition.when_cutting,
        note=("only needed when cutting: blocks the nutrition gate only when some goal has "
              "kind fat_loss or physique_target ripped; otherwise reported when missing, never blocking"),
    ),
    IntakeField(
        name="activity_level", source="Nutrition ch02 (maintenance multipliers 1.3–2.2 by daily-life activity)",
        storage="profile.activity_level", gates=GateDomain.nutrition,
        note="NEAT bracket; the equation is a hypothesis corrected by weekly weight averages",
    ),
    IntakeField(
        name="diet_phase_duration_weeks", source="Nutrition ch05 (≥3 months dieting → schedule diet breaks)",
        storage="profile.diet_phase_duration_weeks", gates=GateDomain.nutrition,
        note="current continuous cut/gain duration, not past diet history",
    ),
    IntakeField(
        name="tracking_tier", source="Nutrition ch07 (four tiers; 'miss = drop a tier and continue')",
        storage="profile.tracking_tier", gates=GateDomain.nutrition,
        note="willingness/experience selects the prescription tier",
    ),
    IntakeField(
        name="eating_out_per_week", source="Nutrition ch08 (frequency caps by phase: prep ~monthly / cut 1–2×/wk)",
        storage="profile.eating_out_per_week", gates=GateDomain.nutrition,
        note="hidden-oil error margin ordering applies",
    ),
    IntakeField(
        name="alcohol_per_week", source="Nutrition ch08 (7 kcal/g; ≤15% of daily kcal; ≤2×/wk)",
        storage="profile.alcohol_per_week", gates=GateDomain.nutrition,
        note="drinks/week; automatic tier-drop while drinking",
    ),
    IntakeField(
        name="supplement_notes", source="Nutrition ch06 (audit whatever stack the user reports via the three-filter test)",
        storage="profile.supplement_notes", gates=GateDomain.nutrition,
        note="freeform current stack; never invent product claims",
    ),
    IntakeField(
        name="caffeine_intake", source="Nutrition ch06 (caffeine dosing is tolerance-dependent; habitual intake is load-bearing)",
        storage="profile.caffeine_intake", gates=GateDomain.nutrition,
        note="freeform, e.g. '2 coffees/day'",
    ),
    IntakeField(
        name="family_diabetes_history", source="Nutrition ch03 (insulin-resistance macro-branch gate)",
        storage="profile.family_diabetes_history", gates=GateDomain.nutrition,
        note="ask-when-relevant (nutrition plan); phrased sensitively",
    ),
    IntakeField(
        name="pcos", source="Nutrition ch03 (insulin-resistance macro-branch gate)",
        storage="profile.pcos", gates=GateDomain.nutrition,
        gate_condition=GateCondition.unless_male,
        note=("ask-when-relevant (nutrition plan); phrased sensitively. Does not apply when sex is "
              "male: blocks only when sex is female or still unknown"),
    ),
    IntakeField(
        name="oligomenorrhea", source="Nutrition ch03 (>35-day cycles; over-represented in strength sports)",
        storage="profile.oligomenorrhea", gates=GateDomain.nutrition,
        gate_condition=GateCondition.unless_male,
        note=("ask-when-relevant (nutrition plan); phrased sensitively. Does not apply when sex is "
              "male: blocks only when sex is female or still unknown"),
    ),
]


class IntakeSummary(BaseModel):
    """Compact assess_intake() result (coach_intake_status default): the same
    keys as IntakeReport, but `fields` are FieldSummary and `next_round` also
    carries the full definitions of the fields it lists."""

    fields: list[FieldSummary] = Field(default_factory=list)
    weeks_since_last_session: float | None = None
    training_ready: bool = False
    nutrition_ready: bool = False
    missing: list[str] = Field(default_factory=list)
    missing_by_gate: dict[str, list[str]] = Field(default_factory=dict)
    progress: dict[str, DomainProgress] = Field(default_factory=dict)
    rounds_total: int = 0
    next_round: DetailedNextRound | None = None
