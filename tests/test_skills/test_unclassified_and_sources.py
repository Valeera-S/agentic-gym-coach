"""Unknown exercises — `unclassified` fallback, keyword precedence, muscle_source.

An unmappable name logs fine but credits no muscle (it used to pour its sets
into `core`). Every logged exercise records how its muscle was decided, and
the needs_review message says so truthfully: guessed vs set by the caller vs
unclassified.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from coach_tools import DISPATCH, error_payload
from models import ExerciseModel, MuscleGroup, MuscleSource, SessionInput, UserProfile, classify
from skills.init import get_duckdb
from skills.session_logger import log_session
from skills.trend_analysis import get_specialization_trend, hard_sets_by_muscle

T = date.today() - timedelta(days=1)
M = MuscleGroup


def _log(*exercises: ExerciseModel):
    return log_session(SessionInput(date=T, exercises=list(exercises)))


def _stored(field: str):
    return get_duckdb().execute(f"SELECT exercises[1].{field} FROM sessions").fetchone()[0]


def _review_details(conf) -> list[str]:
    return [f.detail for f in conf.anomaly_flags if f.code.value == "needs_review"]


# --- unclassified --------------------------------------------------------------------

def test_unmappable_name_logs_as_unclassified_and_credits_no_muscle():
    conf = _log(ExerciseModel(name="Zercher Carry", sets=3, reps=[30, 30, 30]))
    assert _stored("muscle_group") == "unclassified"
    assert _stored("muscle_source") == "unclassified"
    assert _review_details(conf) == [
        "unmapped exercise 'Zercher Carry' is unclassified: logged, but its sets "
        "credit no muscle until it is mapped"]
    assert hard_sets_by_muscle(T - timedelta(days=1), T + timedelta(days=1)) == {}
    for muscle in M:
        if muscle is not M.unclassified:
            assert get_specialization_trend(muscle, window_days=7).effective_volume == 0.0


def test_caller_set_unclassified_on_a_catalog_name_keeps_the_chart_credit():
    _log(ExerciseModel(name="Lat Pulldown", muscle_group="unclassified", sets=2, reps=[10, 10]))
    vol = hard_sets_by_muscle(T - timedelta(days=1), T + timedelta(days=1))
    assert vol == {"lats": 2.0, "biceps": 2.0, "rear_delt": 2.0}


def test_trend_for_unclassified_is_invalid_input():
    with pytest.raises(ValueError) as exc:
        DISPATCH["trend"]({"muscle": "unclassified"})
    assert error_payload(exc.value)["error"] == "invalid_input"


@pytest.mark.parametrize("profile", [
    {"priority_muscles": ["unclassified"]},
    {"goals": [{"kind": "hypertrophy", "target_muscles": ["chest", "unclassified"]}]},
])
def test_unclassified_is_never_a_priority_or_target(profile):
    with pytest.raises(ValidationError):
        UserProfile.model_validate(profile)


# --- muscle_source and truthful wording -------------------------------------------

def test_catalog_name_records_catalog_source_and_no_review():
    conf = _log(ExerciseModel(name="cable crossover", sets=1, reps=[12]))
    assert (_stored("muscle_source"), _stored("muscle_group")) == ("catalog", "chest")
    assert _review_details(conf) == []


def test_keyword_hit_is_reported_as_guessed():
    conf = _log(ExerciseModel(name="Meadows Row", sets=1, reps=[10]))
    assert (_stored("muscle_source"), _stored("muscle_group")) == ("keyword", "lats")
    assert _review_details(conf) == ["unmapped exercise 'Meadows Row' guessed by keyword as lats"]


def test_caller_muscle_on_an_unknown_name_is_reported_as_caller_set():
    conf = _log(ExerciseModel(name="Meadows Row", muscle_group="mid_back", sets=1, reps=[10]))
    assert (_stored("muscle_source"), _stored("muscle_group")) == ("caller", "mid_back")
    details = _review_details(conf)
    assert details == ["exercise 'Meadows Row' is not in the catalog; muscle mid_back set by caller"]
    assert "guessed" not in details[0]


def test_caller_muscle_on_a_catalog_name_is_caller_source_without_review():
    conf = _log(ExerciseModel(name="Dip", muscle_group="chest", sets=1, reps=[8]))
    assert _stored("muscle_source") == "caller"
    assert _review_details(conf) == []


def test_ingest_log_does_not_launder_catalog_guesses_as_caller_set():
    from pathlib import Path
    from scripts.ingest_log import parse_log_md
    sample = Path(__file__).resolve().parents[2] / "docs" / "reference" / "sample_log.md"
    sessions = parse_log_md(sample.read_text(encoding="utf-8"))
    assert len(sessions) > 10
    # the logger classifies each name itself and records how
    assert all(ex.muscle_group is None for s in sessions for ex in s.exercises)


# --- keyword precedence and word boundaries ---------------------------------------

@pytest.mark.parametrize("name, muscle", [
    ("Lateral Lunge", M.quads),            # lunge before lateral
    ("Tricep Kickback", M.triceps),        # tricep before kickback
    ("Leg Press Calf Raise", M.calves),    # calf before leg press
    ("Incline Barbell Row", M.lats),       # row before incline
    ("Chest Supported T-Bar Row", M.lats), # row before chest
    ("Incline Curl", M.biceps),            # curl before incline
    ("Bench Dip", M.triceps),              # dip before bench
    ("Seated Leg Curl", M.hamstrings),     # leg curl before curl
    ("Arnold Press", M.front_delt),        # vertical push: anterior delts
    ("Seated Shoulder Press Machine X", M.front_delt),
    ("Incline Flyes", M.chest),            # plural
    ("Spider Curls", M.biceps),
    ("Sumo Deadlift", M.glutes),           # hinge: first-listed primary
    ("Rear Delt Cable Pull", M.rear_delt),
    ("Weighted Chin-Up", M.lats),
    ("Hanging Knee Crunch", M.core),
    # hyphenated prefixes never trip a bare word
    ("Iso-Lateral Incline Press", M.chest),
    ("Hammer Strength Iso-Lateral Incline Press", M.chest),
    ("Iso-Lateral Chest Press", M.chest),
    ("Iso-Lateral Shoulder Press", M.front_delt),
    ("Iso-Lateral Row", M.lats),
    ("Unilateral Row", M.lats),
    ("Split Stance Row", M.lats),
    ("Split-Stance Cable Row", M.lats),
    # one-word spellings
    ("Skullcrusher", M.triceps),
    ("EZ Bar Skullcrushers", M.triceps),
    ("Hip Thruster", M.glutes),
    ("Benchpress", M.chest),
    # ch03 "Pullover / lat pushdown" is lats, not the triceps pushdown
    ("Straight Arm Pushdown", M.lats),
    ("Straight-Arm Cable Pushdown", M.lats),
    ("Lat Pushdown", M.lats),
    # every rear-delt fly spelling
    ("Reverse Cable Flies", M.rear_delt),
    ("Reverse Cable Fly", M.rear_delt),
    ("Reverse Dumbbell Fly", M.rear_delt),
    ("Reverse Pec Deck Fly", M.rear_delt),
    ("Rear Fly", M.rear_delt),
    ("Bent-Over Dumbbell Fly", M.rear_delt),
    ("Band Pull-Apart", M.rear_delt),
    # specific before general
    ("Upright Row", M.side_delt),
    ("Lateral Band Walk", M.glutes),
    ("Hamstring Curl", M.hamstrings),
    ("Nordic Curl", M.hamstrings),
    ("Pull-Through", M.glutes),
    ("Rack Pull", M.glutes),
    ("Dumbbell Kickback", M.triceps),
    ("Walking Lunge", M.quads),
])
def test_keyword_precedence(name, muscle):
    c = classify(name)
    assert (c.muscle_group, c.source) == (muscle, MuscleSource.keyword), name


@pytest.mark.parametrize("name", [
    "Machine Adductor",       # "machine" contains "chin" — not a chin-up
    "Medicine Ball Throw",    # "throw" contains "row" — not a row
    "Landmine Press",         # "press" alone is ambiguous
    "Farmer's Walk",
    "Shrug",                  # traps are not in the vocabulary
    "Incline Walk",           # cardio, not an incline press
    "Incline Treadmill Walk",
    "Rowing Machine",
])
def test_words_inside_other_words_do_not_match(name):
    assert classify(name).source is MuscleSource.unclassified


# --- safety gate unaffected ---------------------------------------------------------

def test_safety_gate_still_blocks_an_unclassified_name_banned_verbatim():
    from skills.injuries import seed_injury
    from skills.safety_gate import check_exercise_safety
    seed_injury("lower_back", "active", 6, contraindicated_exercises=["Zercher Carry"])
    assert check_exercise_safety("zercher carry").safe is False
    assert check_exercise_safety("Farmer's Walk").safe is True


def test_a_null_entry_in_a_stored_ban_list_is_skipped_not_a_crash():
    from skills.safety_gate import check_exercise_safety
    get_duckdb().execute(
        "INSERT INTO injury_status (location, status, severity, contraindicated_exercises, "
        "safe_alternatives) VALUES ('lower_back', 'active', 5, [NULL, 'Squat'], [])")
    assert check_exercise_safety("Squat").safe is False
    assert check_exercise_safety("Lat Pulldown").safe is True


def test_caller_set_unclassified_says_its_sets_credit_nothing():
    conf = _log(ExerciseModel(name="Zercher Carry", muscle_group="unclassified", sets=1, reps=[1]))
    (detail,) = _review_details(conf)
    assert "set it unclassified" in detail and "credit no muscle" in detail


def test_null_entries_in_stored_alternatives_never_break_the_gate_or_the_list():
    from skills.injuries import list_injuries
    from skills.safety_gate import check_exercise_safety
    get_duckdb().execute(
        "INSERT INTO injury_status (location, status, severity, contraindicated_exercises, "
        "safe_alternatives) VALUES ('lower_back', 'active', 5, ['Squat', NULL], [NULL, 'Leg Press'])")
    r = check_exercise_safety("Squat")
    assert r.safe is False and r.alternatives == ["Leg Press"]
    (injury,) = list_injuries()
    assert injury.contraindicated_exercises == ["Squat"]
    assert injury.safe_alternatives == ["Leg Press"]


@pytest.mark.parametrize("name, muscle", [
    # slang the substring rules used to catch (round 2)
    ("Laterals", M.side_delt), ("Cable Laterals", M.side_delt), ("DB Laterals", M.side_delt),
    ("Single-Arm Lateral", M.side_delt), ("Lateral Delt Raise", M.side_delt),
    ("Unilateral Lateral Raise", M.side_delt),
    ("Butterfly", M.chest), ("Butterfly Machine", M.chest),
    ("Squatting", M.quads), ("Benching", M.chest), ("Deadlifting", M.glutes),
    ("Seated Rowing", M.lats), ("Cable Rowing", M.lats),
    # a reverse nordic is knee extension, not the hamstring nordic
    ("Reverse Nordic", M.quads), ("Reverse Nordic Curl", M.quads), ("Nordic Curl", M.hamstrings),
    # rear-delt raises, whatever the word order
    ("Bent Over Lateral Raise", M.rear_delt), ("Seated Rear Lateral Raise", M.rear_delt),
    ("Reverse Lateral Raise", M.rear_delt), ("Reverse Butterfly", M.rear_delt),
    ("Reverse Grip Cable Lateral Raise", M.side_delt),  # a grip, not a rear-delt raise
    ("Ham Curl", M.hamstrings),
    ("Pike Push-Up", M.front_delt), ("Handstand Push-Up", M.front_delt), ("HSPU", M.front_delt),
    # cheap recoveries of common names
    ("Dumbbell RDL", M.glutes), ("SLDL", M.glutes), ("Kettlebell Swing", M.glutes),
    ("Hanging Knee Raises", M.core), ("Sit Ups", M.core), ("Toes to Bar", M.core),
    ("Facepulls", M.rear_delt), ("Chins", M.lats), ("Leg Ext", M.quads),
    ("French Press", M.triceps), ("Rope Pressdown", M.triceps),
    ("Floor Press", M.chest), ("Decline Dumbbell Press", M.chest), ("Block Pull", M.glutes),
    ("Lateral Lunge", M.quads), ("Reverse Lunge", M.quads), ("Bent Over Row", M.lats),
])
def test_keyword_precedence_round_two(name, muscle):
    c = classify(name)
    assert (c.muscle_group, c.source) == (muscle, MuscleSource.keyword), name


@pytest.mark.parametrize("name", [
    "Wrist Curl", "Reverse Wrist Curls", "Forearm Curl", "Neck Curl",  # not in the vocabulary
    "Row Erg", "Concept2 Row", "Sled Pull", "Lateral Box Jump", "Incline Y Raise", "Rowing",
])
def test_recognised_non_vocabulary_work_is_unclassified(name):
    assert classify(name).source is MuscleSource.unclassified


@pytest.mark.parametrize("name, identity", [
    ("Cable Crossovers", "Cable Fly"), ("Leg Presses", "Leg Press"), ("Dips", "Dip"),
    ("Dumbbell Flyes", "Dumbbell Fly"), ("Dumbbell Flies", "Dumbbell Fly"),
    ("Reverse Flies", "Reverse Fly"),
    ("lat   pulldown", "Lat Pulldown"), ("  Lat\tPulldown ", "Lat Pulldown"),
])
def test_plurals_and_inner_whitespace_resolve_to_the_catalog(name, identity):
    c = classify(name)
    assert (c.name, c.source) == (identity, MuscleSource.catalog)


def test_inner_whitespace_never_decides_a_ban():
    from skills.injuries import seed_injury
    from skills.safety_gate import check_exercise_safety
    seed_injury("left_elbow", "active", 4, contraindicated_exercises=["Lat  Pulldown"])
    assert check_exercise_safety("lat pulldown").safe is False
    assert check_exercise_safety("LAT   PULLDOWNS").safe is False


def test_pathological_names_classify_fast():
    import time
    t0 = time.perf_counter()
    for prefix in ("reverse ", "bent-over ", "rear "):
        classify(prefix + "x" * 24000)
    assert time.perf_counter() - t0 < 0.5


@pytest.mark.parametrize("name, muscle", [
    # abdominals before every chest word (round 3)
    ("Decline Crunch", M.core), ("Decline Sit-Up", M.core), ("Bench Crunch", M.core),
    ("Incline Plank", M.core), ("Forearm Plank", M.core),
    # behind-the-neck lifts are not neck work
    ("Behind the Neck Pulldown", M.lats), ("Behind Neck Lat Pulldown", M.lats),
    ("Behind-the-Neck Press", M.front_delt),
    # "lateral pulldown" is a lat pulldown
    ("Lateral Pulldown", M.lats), ("Lateral Pull-Down", M.lats),
    ("Reverse Cable Crossover", M.rear_delt), ("Reverse Hyper", M.glutes),
    ("GHR", M.hamstrings), ("Glute Ham Raise", M.hamstrings), ("Donkey Kicks", M.glutes),
    ("Bulgarians", M.quads), ("Overhead Dumbbell Press", M.front_delt),
    ("Hex Press", M.chest), ("Pin Press", M.chest),
])
def test_keyword_precedence_round_three(name, muscle):
    c = classify(name)
    assert (c.muscle_group, c.source) == (muscle, MuscleSource.keyword), name


@pytest.mark.parametrize("name", ["Neck Curl", "Neck Harness Extension", "Incline Shrug",
                                  "Incline Y-Raise", "Jefferson Curl", "Lateral Bound",
                                  "Pec Stretch"])
def test_more_non_vocabulary_work_is_unclassified(name):
    assert classify(name).source is MuscleSource.unclassified


def test_ies_plural_ban_still_blocks():
    from skills.injuries import seed_injury
    from skills.safety_gate import check_exercise_safety
    seed_injury("left_shoulder", "active", 4, contraindicated_exercises=["Dumbbell Fly"])
    assert check_exercise_safety("Dumbbell Flies").safe is False
