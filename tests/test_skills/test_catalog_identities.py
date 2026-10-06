"""Exercise identities — split implements, chart-derived muscles, legacy names.

A canonical name is an exercise identity (different implement = different
identity); muscles follow from the ch03 pattern; names stored before the
identity split keep resolving and counting; the safety gate stays fail-closed
across the split.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from models import ExerciseModel, LoadType, MuscleGroup, SessionInput
from models.doctrine_ch03 import (
    DOCUMENTED_EXCEPTIONS,
    EXERCISE_PATTERN,
    HEURISTIC_CLASSIFICATIONS,
    MovementPattern,
)
from models.exercise_catalog import (
    CATALOG,
    LEGACY_ALIASES,
    PRIMARY,
    SECONDARY_OVERLAP,
    ban_match_names,
    canonicalize,
    credited_muscles,
    default_load_type,
    resolve_name,
)
from skills.init import get_duckdb

M = MuscleGroup


def _credits(name: str) -> set[MuscleGroup]:
    return {PRIMARY[name], *SECONDARY_OVERLAP[name]}


# --- identities -----------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    ("Dumbbell Fly", "Cable Fly"),
    ("Smith Incline Press", "Barbell Incline Bench Press"),
    ("Dumbbell Bench Press", "Barbell Bench Press"),
    ("Machine Chest Press", "Barbell Bench Press"),
    ("Dumbbell Skull Crusher", "Skull Crusher"),
    ("Dumbbell Lateral Raise", "Cable Lateral Raise"),
])
def test_different_implements_are_distinct_identities(a, b):
    assert canonicalize(a)[0] == a
    assert canonicalize(b)[0] == b
    assert a != b


@pytest.mark.parametrize("alias, identity", [
    ("cable crossover", "Cable Fly"),
    ("PEC DECK", "Machine Fly"),
    ("Incline Dumbbell Press", "Dumbbell Incline Press"),
    ("Wide Grip Pulldown", "Lat Pulldown"),
    ("Penlay Row", "Pendlay Row"),
    ("Close Grip Pull-Up", "Pull-Up"),
])
def test_aliases_are_true_synonyms_of_one_identity(alias, identity):
    name, _mg, review = canonicalize(alias)
    assert (name, review) == (identity, False)


def test_every_alias_maps_to_exactly_one_identity():
    seen: dict[str, str] = {}
    for e in CATALOG.values():
        for n in (e.name, *e.aliases):
            key = n.strip().lower()
            assert key not in seen, f"{n!r} claimed by {seen.get(key)} and {e.name}"
            seen[key] = e.name


def test_generic_identities_declare_no_load_type_and_real_variants():
    for e in CATALOG.values():
        if e.generic:
            assert e.load_type is None, e.name  # implement unknown: nothing assumed
        for v in e.variants:
            assert v in CATALOG and not CATALOG[v].generic, (e.name, v)
        if e.variants:
            assert e.generic, f"{e.name} lists variants but is not generic"


@pytest.mark.parametrize("name, load_type", [
    ("Dumbbell Bench Press", LoadType.per_hand),
    ("Cable Fly", LoadType.per_side),
    ("Lat Pulldown", LoadType.machine_stack),
    ("Barbell Bench Press", LoadType.total),
    ("Pull-Up", LoadType.bodyweight),
    ("Bench Press", None),
    ("not an exercise", None),
])
def test_load_type_defaults(name, load_type):
    assert default_load_type(name) == load_type


# --- muscles from the chart --------------------------------------------------------

def test_flat_bench_credits_chest_front_delt_triceps_not_side_delt():
    for flat in ("Bench Press", "Barbell Bench Press", "Dumbbell Bench Press",
                 "Smith Bench Press", "Machine Chest Press"):
        assert _credits(flat) == {M.chest, M.front_delt, M.triceps}, flat
        assert PRIMARY[flat] is M.chest


def test_incline_press_also_credits_side_delt():
    for incline in ("Incline Bench Press", "Barbell Incline Bench Press",
                    "Dumbbell Incline Press", "Smith Incline Press", "Machine Incline Press"):
        assert _credits(incline) == {M.chest, M.front_delt, M.triceps, M.side_delt}, incline


def test_close_grip_and_dips_make_triceps_primary():
    for name in ("Close-Grip Bench Press", "Dip"):
        assert PRIMARY[name] is M.triceps
        assert _credits(name) == {M.chest, M.front_delt, M.triceps}


def test_vertical_push_is_front_delt_primary():
    for name in ("Shoulder Press", "Barbell Overhead Press", "Dumbbell Shoulder Press"):
        assert PRIMARY[name] is M.front_delt
        assert _credits(name) == {M.front_delt, M.triceps, M.side_delt}


def test_romanian_deadlift_credits_erectors_and_mid_back():
    assert _credits("Romanian Deadlift") == {M.glutes, M.hamstrings, M.erectors, M.mid_back}


def test_free_weight_squats_credit_erectors_machine_squats_do_not():
    assert M.erectors in _credits("Squat")
    assert M.erectors in _credits("Bulgarian Split Squat")
    assert _credits("Leg Press") == {M.quads, M.glutes}


def test_isolation_credits_only_its_target():
    for name, pattern in EXERCISE_PATTERN.items():
        if pattern is MovementPattern.isolation:
            assert SECONDARY_OVERLAP[name] == [], name
            assert PRIMARY[name] is CATALOG[name].target, name


def test_documented_exceptions_carry_a_chart_reason():
    for name, exc in DOCUMENTED_EXCEPTIONS.items():
        assert name in CATALOG, name
        assert "ch03" in exc.reason, name


def test_face_pull_is_heuristic_isolation_without_a_citation():
    assert EXERCISE_PATTERN["Face Pull"] is MovementPattern.isolation
    assert HEURISTIC_CLASSIFICATIONS["Face Pull"].startswith("HEURISTIC")
    assert _credits("Face Pull") == {M.rear_delt}


def test_heuristic_classifications_are_labelled():
    for name, why in HEURISTIC_CLASSIFICATIONS.items():
        assert name in CATALOG and why.startswith("HEURISTIC:"), name


# --- legacy names ---------------------------------------------------------------------

@pytest.mark.parametrize("legacy", ["Fly", "Bench Press", "Incline Bench Press",
                                    "Dumbbell Bench Press", "Face Pull", "Lat Pulldown"])
def test_legacy_stored_names_still_resolve_to_a_pattern(legacy):
    assert resolve_name(legacy) == legacy
    assert legacy in EXERCISE_PATTERN


def test_every_legacy_canonical_name_is_still_a_catalog_identity():
    for legacy in LEGACY_ALIASES:
        assert resolve_name(legacy) == legacy, legacy


def test_historical_rows_under_legacy_names_still_count():
    """Rows stored before the split (and before 0003's remap made upper_chest
    chest) keep crediting their identity's chart muscles — front_delt included,
    which the old catalog could not express."""
    from skills.trend_analysis import hard_sets_by_muscle
    today = date.today()
    get_duckdb().execute(
        "INSERT INTO sessions (date, phase, exercises) VALUES (?, 'maintenance', ?)",
        [today - timedelta(days=1), [
            {"name": "Fly", "muscle_group": "chest", "sets": 3},
            {"name": "Dumbbell Bench Press", "muscle_group": "chest", "sets": 4},
            {"name": "Shoulder Press", "muscle_group": "side_delt", "sets": 2},
        ]],
    )
    vol = hard_sets_by_muscle(today - timedelta(days=7), today)
    assert vol["chest"] == pytest.approx(7.0)
    assert vol["front_delt"] == pytest.approx(3 + 4 + 2)
    assert vol["triceps"] == pytest.approx(4 + 2)
    # the legacy Shoulder Press row stored side_delt as its primary; it is
    # credited once even though side_delt is also a derived secondary
    assert vol["side_delt"] == pytest.approx(2.0)


def test_rows_stored_under_a_now_alias_name_resolve_to_the_identity():
    from skills.trend_analysis import hard_sets_by_muscle
    today = date.today()
    get_duckdb().execute(
        "INSERT INTO sessions (date, phase, exercises) VALUES (?, 'maintenance', ?)",
        [today, [{"name": "pec deck", "muscle_group": "chest", "sets": 2}]],
    )
    vol = hard_sets_by_muscle(today - timedelta(days=1), today)
    assert vol == {"chest": 2.0, "front_delt": 2.0}


# --- each muscle at most once per set ------------------------------------------------

def test_stored_primary_that_is_also_a_secondary_is_not_double_counted():
    from skills.session_logger import log_session
    from skills.trend_analysis import get_specialization_trend, hard_sets_by_muscle
    today = date.today()
    log_session(SessionInput(date=today, exercises=[
        ExerciseModel(name="Dip", muscle_group="chest", sets=2, reps=[8, 8]),
    ]))
    vol = hard_sets_by_muscle(today - timedelta(days=1), today)
    assert vol["chest"] == pytest.approx(2.0)
    report = get_specialization_trend(M.chest, window_days=7)
    assert report.effective_volume == pytest.approx(2.0)
    assert report.detail["overlap_sets"] == pytest.approx(0.0)
    assert credited_muscles("Dip", "chest") == {"chest", "front_delt", "triceps"}


def test_raw_name_is_stored_beside_the_identity():
    from skills.session_logger import log_session
    log_session(SessionInput(date=date(2026, 10, 1), exercises=[
        ExerciseModel(name="  cable crossover ", sets=1, reps=[12], weight_kg=[7.5]),
    ]))
    name, raw = get_duckdb().execute(
        "SELECT exercises[1].name, exercises[1].raw_name FROM sessions").fetchone()
    assert (name, raw) == ("Cable Fly", "  cable crossover ")


# --- safety gate across the split -------------------------------------------------------

def _ban(*names: str) -> None:
    from skills.injuries import seed_injury
    seed_injury("left_shoulder", "active", 5, contraindicated_exercises=list(names),
                safe_alternatives=["Lateral Raise"])


def _blocked(name: str) -> bool:
    from skills.safety_gate import check_exercise_safety
    return not check_exercise_safety(name).safe


def _seed_raw_ban(*names: str) -> None:
    """A ban row as an earlier version stored it (canonical names of that time)."""
    get_duckdb().execute(
        "INSERT INTO injury_status (location, status, severity, contraindicated_exercises, "
        "safe_alternatives) VALUES ('left_shoulder', 'active', 5, ?, ['Lateral Raise'])",
        [list(names)],
    )


@pytest.mark.parametrize("query", ["Dumbbell Bench Press", "dumbbell bench press",
                                   "Barbell Bench Press", "Machine Chest Press",
                                   "chest press", "Bench Press", "FLAT BENCH"])
def test_pre_split_ban_still_blocks_every_identity_it_covered(query):
    # stored by the old catalog, e.g. from the user saying "dumbbell bench press"
    _seed_raw_ban("Bench Press")
    assert _blocked(query)


@pytest.mark.parametrize("query", ["Cable Fly", "Dumbbell Fly", "pec deck", "Machine Fly"])
def test_pre_split_fly_ban_blocks_all_flys(query):
    _seed_raw_ban("Fly")
    assert _blocked(query)


def test_generic_query_is_blocked_by_a_ban_on_any_variant():
    _ban("Smith Bench Press")
    assert _blocked("Bench Press")       # might be the Smith press: fail-closed
    assert _blocked("smith machine bench press")
    assert not _blocked("Dumbbell Bench Press")  # a sibling variant is its own identity


def test_ban_on_a_generic_name_covers_its_variants():
    _ban("shoulder press")
    for q in ("Dumbbell Shoulder Press", "Overhead Press", "Machine Shoulder Press"):
        assert _blocked(q), q
    assert not _blocked("Lateral Raise")


def test_ban_stored_verbatim_before_the_name_entered_the_catalog_still_blocks():
    _seed_raw_ban("Pec Deck")  # unmapped when it was seeded, so stored as typed
    assert _blocked("Machine Fly")
    assert _blocked("pec deck fly")


def test_unrelated_exercises_stay_safe():
    _ban("Bench Press")
    for q in ("Lat Pulldown", "Squat", "Cable Fly", "Shoulder Press"):
        assert not _blocked(q), q


def test_ban_match_names_is_fail_closed_for_generics():
    assert ban_match_names("Bench Press") >= {
        "Barbell Bench Press", "Dumbbell Bench Press", "Smith Bench Press",
        "Machine Chest Press", "Close-Grip Bench Press"}
    assert ban_match_names("Dumbbell Bench Press") == {"Dumbbell Bench Press", "Bench Press"}


@pytest.mark.parametrize("query", ["Kickback", "kickback", "KICKBACK", "  kickback "])
def test_ban_matching_is_case_insensitive_across_the_split(query):
    # "Kickback" was an alias of Cable Kickback and no longer resolves; a ban
    # stored under the old canonical name must block it in every casing.
    _seed_raw_ban("Cable Kickback")
    assert _blocked(query)


# --- additions signed off by the human (F5 verdict) -----------------------------

def test_chest_supported_dumbbell_row_is_a_per_hand_horizontal_pull():
    assert canonicalize("Incline Dumbbell Row")[0] == "Chest-Supported Dumbbell Row"
    assert EXERCISE_PATTERN["Chest-Supported Dumbbell Row"] is MovementPattern.horizontal_pull
    assert default_load_type("Chest-Supported DB Row") is LoadType.per_hand
    assert _credits("Chest-Supported Dumbbell Row") == _credits("Cable Row")


def test_bodyweight_squat_withholds_erectors():
    assert canonicalize("air squat")[0] == "Bodyweight Squat"
    assert _credits("Bodyweight Squat") == {M.quads, M.glutes}
    assert "ch03" in DOCUMENTED_EXCEPTIONS["Bodyweight Squat"].reason
    assert default_load_type("BW Squat") is LoadType.bodyweight


def test_converging_pulldown_machine_is_not_the_cable_pulldown():
    assert canonicalize("Iso-Lateral Lat Pulldown")[0] == "Machine Lat Pulldown"
    assert canonicalize("Lat Pulldown")[0] == "Lat Pulldown"
    assert _credits("Machine Lat Pulldown") == _credits("Lat Pulldown")


def test_legacy_lat_pulldown_ban_still_blocks_the_converging_machine():
    _seed_raw_ban("Lat Pulldown")  # stored before the split, logged on the machine
    assert _blocked("Machine Lat Pulldown") and _blocked("converging lat pulldown")


def test_supinated_lateral_raise_is_a_grip_alias():
    for alias in ("Reverse Grip Lateral Raise", "reverse-grip dumbbell lateral raise",
                  "Supinated Lateral Raise"):
        assert canonicalize(alias)[0] == "Dumbbell Lateral Raise"


def test_generic_squat_and_row_cover_the_new_identities():
    assert "Bodyweight Squat" in ban_match_names("squat")
    assert "Chest-Supported Dumbbell Row" in ban_match_names("Row")


def test_no_two_identities_collide_under_lookup_key():
    # P44: lookup_key is the single normalization owner; a stronger key must
    # still keep every catalog name/alias on exactly one identity.
    from models.exercise_catalog import CATALOG, lookup_key, resolve_name
    owner: dict[str, str] = {}
    for ident, ex in CATALOG.items():
        for name in (ident, *ex.aliases):
            key = lookup_key(name)
            assert key, name
            assert owner.setdefault(key, ident) == ident, (name, owner[key], ident)
            assert resolve_name(name) == ident, name
