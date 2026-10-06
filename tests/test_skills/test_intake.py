"""intake unit tests — bucket-list scan, per-domain soft gates, drift guards."""

import json
import re
from datetime import date, timedelta

import pytest

from models import (
    INTAKE_CHECKLIST,
    INTAKE_ROUND_TITLES,
    ActivityLevel,
    EquipmentAccess,
    GateDomain,
    AvailabilityWindow,
    ExerciseModel,
    FieldStatus,
    GateCondition,
    Goal,
    GoalKind,
    MuscleGroup,
    PhysiqueTarget,
    SessionInput,
    Sex,
    StressLevel,
    TrackingTier,
    TrainingAge,
    UserProfile,
    Weekday,
)
from conftest import log_empty_session
from skills.injuries import seed_injury
from skills.init import get_duckdb
from skills.intake import assess_intake
from skills.profile import get_profile, set_profile
from skills.session_logger import log_session

TODAY = date.today()


def _full_profile() -> UserProfile:
    """Every checklist-backed profile field set — the scan must call it ready."""
    return UserProfile(
        display_name="Test User",
        goals=[Goal(kind=GoalKind.hypertrophy, target_muscles=[MuscleGroup.quads])],
        priority_muscles=[MuscleGroup.quads],
        training_age=TrainingAge.intermediate,
        days_per_week=4,
        life_stress=StressLevel.moderate,
        liked_exercises=["Squat"],
        disliked_exercises=["Crunch"],
        concurrent_sports=["climbing"],
        rpe_calibrated=True,
        has_tested_maxes=False,
        session_length_min=60,
        equipment_access="home",
        weekly_availability=[
            AvailabilityWindow(weekday=Weekday.fri, start="19:00", end="20:00",
                               venue="school gym"),
        ],
        sex=Sex.female,
        age_years=30,
        bodyweight_kg=60.0,
        bodyfat_pct=25.0,
        activity_level=ActivityLevel.active,
        diet_phase_duration_weeks=10,
        tracking_tier=TrackingTier.better,
        eating_out_per_week=1,
        alcohol_per_week=2,
        supplement_notes="creatine 5g",
        caffeine_intake="1 coffee/day",
        family_diabetes_history=False,
        pcos=False,
        oligomenorrhea=False,
    )


def _training_only_profile() -> UserProfile:
    """Both-gated + training-gated fields only — training ready, nutrition not."""
    return UserProfile(
        goals=[Goal(kind=GoalKind.strength)],
        priority_muscles=[],
        training_age=TrainingAge.novice,
        days_per_week=3,
        life_stress=StressLevel.low,
        liked_exercises=["Bench Press"],
        disliked_exercises=[],
        concurrent_sports=[],
        rpe_calibrated=False,
        has_tested_maxes=True,
        session_length_min=45,
        equipment_access="minimal",
    )


def _names(report):
    return {f.name for f in report.fields}


def test_empty_database_reports_everything_missing():
    report = assess_intake(today=TODAY)
    assert _names(report) == {f.name for f in INTAKE_CHECKLIST}
    assert all(f.status is FieldStatus.missing for f in report.fields)
    assert report.missing == [f.name for f in INTAKE_CHECKLIST]  # checklist order
    assert report.training_ready is False
    assert report.nutrition_ready is False
    assert report.weeks_since_last_session is None  # no sessions at all
    assert report.missing_by_gate["training"] and report.missing_by_gate["nutrition"]


def test_full_intake_is_ready_for_both_domains():
    set_profile(_full_profile())
    seed_injury("left_elbow", "resolving", 3)
    log_empty_session(TODAY - timedelta(days=1))
    report = assess_intake(today=TODAY)
    assert report.missing == []
    assert report.training_ready is True
    assert report.nutrition_ready is True
    assert report.weeks_since_last_session == 0.1
    assert report.missing_by_gate == {"training": [], "nutrition": []}


def test_training_only_profile_gates_nutrition_not_training():
    set_profile(_training_only_profile())
    report = assess_intake(today=TODAY)
    assert report.training_ready is True
    assert report.nutrition_ready is False
    assert report.missing_by_gate["training"] == []
    assert "bodyweight_kg" in report.missing_by_gate["nutrition"]
    # both-gated fields were satisfied, so they gate nothing
    assert "goals" not in report.missing_by_gate["nutrition"]


def test_injury_table_satisfies_injuries_field_without_profile():
    seed_injury("lower_back", "chronic_baseline", 2)
    report = assess_intake(today=TODAY)
    injuries = next(f for f in report.fields if f.name == "injuries")
    assert injuries.status is FieldStatus.collected
    assert injuries.value  # the stored rows, JSON-safe
    assert "injuries" not in report.missing


def test_absent_injury_rows_are_missing_not_assumed_clear():
    report = assess_intake(today=TODAY)
    assert "injuries" in report.missing


def test_explicit_false_is_collected_not_missing():
    set_profile(UserProfile(family_diabetes_history=False))
    report = assess_intake(today=TODAY)
    field = next(f for f in report.fields if f.name == "family_diabetes_history")
    assert field.status is FieldStatus.collected
    assert field.value is False


def test_report_serializes_to_json():
    set_profile(_full_profile())
    report = assess_intake(today=TODAY)
    payload = json.loads(report.model_dump_json())  # enums/goals must be JSON-safe
    goals = next(f for f in payload["fields"] if f["name"] == "goals")
    assert goals["value"][0]["kind"] == "hypertrophy"


def test_every_checklist_field_cites_a_source_or_heuristic():
    # Drift guard: an unsourced field is exactly the v1 mistake this repo removed.
    for f in INTAKE_CHECKLIST:
        ok = f.source.startswith("HEURISTIC") or re.search(r"ch0[1-9]", f.source)
        assert ok, f


def test_checklist_storage_paths_resolve_against_profile_schema():
    for f in INTAKE_CHECKLIST:
        if f.storage == "injury_status":
            continue
        assert f.storage.startswith("profile."), f
        assert f.storage.split(".", 1)[1] in UserProfile.model_fields, f


def test_profile_without_goals_reports_missing_and_closes_both_gates():
    # Empty goals is "never asked", not an answer — 'no specific goal' is
    # recorded as kind=general_fitness. Both gates must stay closed.
    set_profile(UserProfile(
        training_age=TrainingAge.novice,
        days_per_week=3,
        bodyweight_kg=70.0,
        sex=Sex.male,
        activity_level=ActivityLevel.active,
    ))
    report = assess_intake(today=TODAY)
    goals = next(f for f in report.fields if f.name == "goals")
    assert goals.status is FieldStatus.missing
    assert report.training_ready is False
    assert report.nutrition_ready is False
    assert "goals" in report.missing_by_gate["training"]
    assert "goals" in report.missing_by_gate["nutrition"]


def test_unanswered_equipment_access_is_missing_not_default_full_gym():
    set_profile(UserProfile(sex=Sex.male))  # any profile; equipment never answered
    report = assess_intake(today=TODAY)
    equipment = next(f for f in report.fields if f.name == "equipment_access")
    assert equipment.status is FieldStatus.missing
    assert equipment.value is None


def test_empty_weekly_availability_is_missing():
    set_profile(UserProfile(sex=Sex.male))  # availability never asked
    report = assess_intake(today=TODAY)
    field = next(f for f in report.fields if f.name == "weekly_availability")
    assert field.status is FieldStatus.missing
    assert field.value is None


def test_fluid_fields_never_hold_gates_open():
    # Discovered/pattern fields (preferences, availability) are reported when
    # missing but must never block a domain; strict-empty is declared on
    # exactly the fields where empty is indistinguishable from never-asked.
    flags = {f.name: (f.blocks_gate, f.empty_means_missing) for f in INTAKE_CHECKLIST}
    for name in ("exercise_likes", "exercise_dislikes", "weekly_availability"):
        assert flags[name][0] is False, name
    assert {n for n, (_, emm) in flags.items() if emm} == {"goals", "weekly_availability"}


def test_omitted_fields_are_gone_from_schema_and_checklist():
    # ADR 0002: meal frequency is book-neutral, social support unmeasurable,
    # priority muscles derived — none of them is an intake question.
    names = {f.name for f in INTAKE_CHECKLIST}
    assert {"meals_per_day", "social_support", "priority_muscles"}.isdisjoint(names)
    assert "meals_per_day" not in UserProfile.model_fields
    assert "social_support" not in UserProfile.model_fields


# --- P52: never-asked vs asked-none, and conditional blocking ---------------------------

def _field(report, name):
    return next(f for f in report.fields if f.name == name)


def test_never_mentioned_concurrent_sports_is_missing_and_blocks_training():
    base = _training_only_profile().model_copy(update={"concurrent_sports": None})
    set_profile(base)
    report = assess_intake(today=TODAY)
    f = _field(report, "concurrent_sports")
    assert f.status is FieldStatus.missing and f.value is None
    assert report.training_ready is False
    assert report.missing_by_gate["training"] == ["concurrent_sports"]


def test_a_profile_that_omits_concurrent_sports_defaults_to_never_asked():
    assert UserProfile().concurrent_sports is None
    assert UserProfile.model_validate({"days_per_week": 3}).concurrent_sports is None


def test_explicit_empty_concurrent_sports_is_collected_none():
    set_profile(_training_only_profile())  # concurrent_sports=[] explicitly
    report = assess_intake(today=TODAY)
    f = _field(report, "concurrent_sports")
    assert f.status is FieldStatus.collected and f.value == []
    assert report.training_ready is True


def test_stored_payloads_keep_their_meaning():
    """No migration: profiles are JSON payloads. A stored [] (explicit, or written
    before this fix) stays collected; a payload without the key reads as never asked."""
    con = get_duckdb()
    for payload in ('{"days_per_week": 3, "concurrent_sports": []}', '{"days_per_week": 3}'):
        con.execute("DELETE FROM user_profiles")
        con.execute("INSERT INTO user_profiles (updated_at, payload) VALUES (now(), ?)", [payload])
        status = _field(assess_intake(today=TODAY), "concurrent_sports").status
        expected = FieldStatus.collected if "concurrent_sports" in payload else FieldStatus.missing
        assert status is expected, payload


def test_concurrent_sports_round_trips_through_storage():
    set_profile(UserProfile(concurrent_sports=[]))
    assert get_profile().concurrent_sports == []
    set_profile(UserProfile(concurrent_sports=["climbing"]))
    assert get_profile().concurrent_sports == ["climbing"]
    set_profile(UserProfile(display_name="x"))
    assert get_profile().concurrent_sports is None


def _male_complete(**update) -> UserProfile:
    """Complete male profile: everything except pcos / oligomenorrhea / bodyfat_pct."""
    return _full_profile().model_copy(update={
        "sex": Sex.male, "pcos": None, "oligomenorrhea": None, "bodyfat_pct": None, **update})


def test_a_complete_male_profile_can_be_nutrition_ready():
    set_profile(_male_complete())
    seed_injury("left_elbow", "resolving", 3)
    report = assess_intake(today=TODAY)
    assert report.nutrition_ready is True and report.training_ready is True
    assert report.missing_by_gate["nutrition"] == []
    # still reported as missing, they just do not block
    assert {"pcos", "oligomenorrhea", "bodyfat_pct"} <= set(report.missing)
    assert all(_field(report, n).blocks_now is False for n in ("pcos", "oligomenorrhea", "bodyfat_pct"))


@pytest.mark.parametrize("sex", [Sex.female, None])
def test_pcos_and_oligomenorrhea_still_block_for_female_or_unknown_sex(sex):
    set_profile(_male_complete(sex=sex, bodyfat_pct=25.0))
    report = assess_intake(today=TODAY)
    assert report.nutrition_ready is False
    assert {"pcos", "oligomenorrhea"} <= set(report.missing_by_gate["nutrition"])


@pytest.mark.parametrize("goals, blocks", [
    ([Goal(kind=GoalKind.fat_loss)], True),
    ([Goal(kind=GoalKind.hypertrophy, physique_target=PhysiqueTarget.ripped)], True),
    ([Goal(kind=GoalKind.hypertrophy), Goal(kind=GoalKind.fat_loss)], True),
    ([Goal(kind=GoalKind.hypertrophy, physique_target=PhysiqueTarget.athletic)], False),
    ([Goal(kind=GoalKind.strength)], False),
])
def test_bodyfat_blocks_only_when_cutting(goals, blocks):
    set_profile(_male_complete(goals=goals))
    report = assess_intake(today=TODAY)
    assert ("bodyfat_pct" in report.missing_by_gate["nutrition"]) is blocks
    assert report.nutrition_ready is (not blocks)
    assert "bodyfat_pct" in report.missing


def test_family_diabetes_history_still_blocks_a_male_profile():
    set_profile(_male_complete(family_diabetes_history=None))
    report = assess_intake(today=TODAY)
    assert report.nutrition_ready is False
    assert "family_diabetes_history" in report.missing_by_gate["nutrition"]


def test_gate_conditions_are_declared_in_the_checklist_notes():
    by_name = {f.name: f for f in INTAKE_CHECKLIST}
    for name, word in (("pcos", "male"), ("oligomenorrhea", "male"), ("bodyfat_pct", "fat_loss")):
        assert by_name[name].gate_condition is not GateCondition.always, name
        assert word in by_name[name].note, name


# --- guided rounds, progress, options ----------------------------------------------------

def _training_blockers() -> list:
    return [f for f in INTAKE_CHECKLIST
            if f.blocks_gate and f.gates in (GateDomain.training, GateDomain.both)]


def test_empty_profile_points_at_round_one_with_zero_progress():
    report = assess_intake(today=TODAY)
    assert report.rounds_total == 3
    assert report.next_round is not None and report.next_round.round == 1
    assert report.next_round.fields == ["goals", "training_age", "days_per_week", "session_length_min"]
    assert report.progress["training"].done == 0
    assert report.progress["training"].total == 9
    assert report.progress["nutrition"].done == 0
    assert report.progress["nutrition"].total == len(report.missing_by_gate["nutrition"])


def test_partial_profile_advances_to_round_two():
    set_profile(UserProfile(
        goals=[Goal(kind=GoalKind.strength)], training_age=TrainingAge.novice,
        days_per_week=3, session_length_min=45))
    report = assess_intake(today=TODAY)
    assert report.next_round.round == 2
    assert report.next_round.fields == ["life_stress", "concurrent_sports", "equipment_access"]
    assert report.progress["training"].done == 4 and report.progress["training"].total == 9


def test_round_with_only_nonblocking_gaps_is_done():
    """Everything training-gating is collected but no injury rows: injuries stays
    in `missing` (non-blocking), yet no round is pending and the gate is open."""
    set_profile(_training_only_profile())
    report = assess_intake(today=TODAY)
    assert "injuries" in report.missing
    assert report.training_ready is True
    assert report.next_round is None
    assert report.progress["training"].done == report.progress["training"].total == 9


def test_pending_round_lists_nonblocking_fields_too():
    set_profile(_training_only_profile().model_copy(update={"rpe_calibrated": None}))
    report = assess_intake(today=TODAY)
    assert report.next_round.round == 3
    assert report.next_round.fields == ["injuries", "rpe_calibrated"]


def test_cutting_goal_changes_nutrition_total():
    set_profile(_male_complete(goals=[Goal(kind=GoalKind.strength)]))
    base = assess_intake(today=TODAY).progress["nutrition"]
    set_profile(_male_complete(goals=[Goal(kind=GoalKind.hypertrophy,
                                           physique_target=PhysiqueTarget.ripped)]))
    cut = assess_intake(today=TODAY).progress["nutrition"]
    assert cut.total == base.total + 1
    assert cut.done == base.done  # bodyfat missing in both
    set_profile(_male_complete(goals=[Goal(kind=GoalKind.fat_loss)], bodyfat_pct=15.0))
    after = assess_intake(today=TODAY).progress["nutrition"]
    assert after.total == cut.total
    assert after.done == cut.done + 1  # bodyfat now collected and counted


def test_every_training_blocker_has_a_guided_round():
    for f in _training_blockers():
        assert f.round in (1, 2, 3), f.name
    for name in ("weekly_availability", "exercise_likes", "exercise_dislikes"):
        assert next(f for f in INTAKE_CHECKLIST if f.name == name).round is None
    assert all(f.round is None for f in INTAKE_CHECKLIST if f.gates is GateDomain.nutrition)


def test_round_assignment_is_exactly_the_agreed_plan():
    plan = {1: ["goals", "training_age", "days_per_week", "session_length_min"],
            2: ["life_stress", "concurrent_sports", "equipment_access"],
            3: ["injuries", "rpe_calibrated", "has_tested_maxes"]}
    for rnd, names in plan.items():
        assert sorted(f.name for f in INTAKE_CHECKLIST if f.round == rnd) == sorted(names)
    assert set(INTAKE_ROUND_TITLES) == {1, 2, 3}


def test_every_rounded_field_has_a_question():
    for f in INTAKE_CHECKLIST:
        if f.round is not None:
            assert f.question.strip().endswith("?"), f.name


_OPTION_ENUMS = {
    "physique_target": PhysiqueTarget, "training_age": TrainingAge,
    "life_stress": StressLevel, "equipment_access": EquipmentAccess,
}


def test_every_option_value_is_a_real_enum_value():
    seen = 0
    for f in INTAKE_CHECKLIST:
        if not f.options:
            continue
        key = f.options_key or f.name
        values = [o.value for o in f.options]
        assert len(values) == len(set(values)), f.name
        if key in _OPTION_ENUMS:
            # every enum value is offered: no choice is hidden from the user
            assert set(values) == {e.value for e in _OPTION_ENUMS[key]}, f.name
        else:  # bool-typed profile field
            assert key in UserProfile.model_fields and set(values) == {"true", "false"}, f.name
        seen += 1
    assert seen >= 5


def test_every_option_is_grounded_and_explains_its_consequence():
    for f in INTAKE_CHECKLIST:
        for o in f.options:
            assert o.means.strip() and o.effect.strip(), (f.name, o.value)
            assert (o.source.startswith("HEURISTIC")
                    or re.search(r"(Training|Nutrition) ch0[1-9]", o.source)), (f.name, o.value)


def test_ripped_option_says_it_switches_on_cut_rules():
    goals = next(f for f in INTAKE_CHECKLIST if f.name == "goals")
    assert goals.options_key == "physique_target"
    ripped = next(o for o in goals.options if o.value == "ripped")
    assert "cut" in ripped.effect.lower() and "body" in ripped.effect.lower()


def test_report_fields_carry_round_question_and_options():
    payload = json.loads(assess_intake(today=TODAY).model_dump_json())
    ta = next(f for f in payload["fields"] if f["name"] == "training_age")
    assert ta["round"] == 1 and ta["question"] and len(ta["options"]) == 3
    assert {"progress", "rounds_total", "next_round"} <= payload.keys()
