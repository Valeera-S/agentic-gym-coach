"""exercise_catalog/canonicalize edge cases — unknowns, case, aliases, overlap maps."""

from models import MuscleGroup
from models.exercise_catalog import (
    SECONDARY_OVERLAP, _ALIAS_TO_CANONICAL, canonicalize, lookup_key,
    secondary_exercises,
)


def test_exact_canonical_name_round_trips():
    name, mg, review = canonicalize("Skull Crusher")
    assert (name, mg, review) == ("Skull Crusher", MuscleGroup.triceps, False)


def test_alias_hit_maps_to_canonical():
    # "Overhead Press" names the barbell press; vertical push primary = front_delt (ch03)
    assert canonicalize("Overhead Press") == ("Barbell Overhead Press", MuscleGroup.front_delt, False)


def test_alias_hits_are_case_insensitive():
    # casing must not decide whether a catalog name maps: an unmapped name
    # keeps its raw spelling, which the safety gate then fails to match
    for raw in ("overhead press", "OVERHEAD PRESS", "  Overhead Press  "):
        assert canonicalize(raw) == ("Barbell Overhead Press", MuscleGroup.front_delt, False)


def test_hyphen_space_underscore_spellings_resolve_to_one_identity():
    # P24: '-' and '_' read as spaces, so "db row" spellings reach the alias
    for raw in ("chest supported db row", "Chest-Supported DB Row", "chest_supported_db_row",
                "chest supported dumbbell row", "Chest-Supported Dumbbell Row"):
        assert canonicalize(raw)[0] == "Chest-Supported Dumbbell Row", raw
    for raw in ("pull up", "Pull-Up", "pull_up"):
        assert canonicalize(raw)[0] == "Pull-Up", raw


def test_close_grip_bench_is_an_alias_of_close_grip_bench_press():
    for raw in ("Close Grip Bench", "close-grip bench", "Close Grip Bench Press"):
        assert canonicalize(raw)[0] == "Close-Grip Bench Press", raw


def test_lookup_key_normalization_contract():
    assert lookup_key("  Chest_Supported--Dumbbell   Row ") == "chest supported dumbbell row"


def test_case_variant_keeps_its_secondary_overlap():
    # "incline bench press" used to miss the table, so trend_analysis credited
    # no secondaries and under-counted triceps/side_delt hard sets
    name, _mg, review = canonicalize("incline bench press")
    assert (name, review) == ("Incline Bench Press", False)
    assert set(SECONDARY_OVERLAP[name]) == {MuscleGroup.front_delt, MuscleGroup.triceps,
                                            MuscleGroup.side_delt}


def test_keyword_fallback_returns_raw_name_with_review():
    name, mg, review = canonicalize("Meadows Row")
    # rows guess the ch03 horizontal-pull first-listed primary, as the catalog stores
    assert (name, mg, review) == ("Meadows Row", MuscleGroup.lats, True)


def test_unknown_exercise_is_unclassified_not_a_real_muscle():
    # the old terminal default poured unknown sets into core
    name, mg, review = canonicalize("Zercher Carry")
    assert review is True
    assert mg == MuscleGroup.unclassified
    assert name == "Zercher Carry"


def test_empty_string_is_unclassified():
    name, mg, review = canonicalize("")
    assert (name, mg, review) == ("", MuscleGroup.unclassified, True)


def test_unicode_name_is_flagged_not_rejected():
    name, mg, review = canonicalize("デッドリフト")
    assert review is True
    assert name == "デッドリフト"


def test_sqlish_name_passes_through_literally():
    s = "Robert'); DROP TABLE sessions;--"
    name, mg, review = canonicalize(s)
    assert name == s and review is True


def test_secondary_overlap_reverse_map_is_consistent():
    for canon, secondaries in SECONDARY_OVERLAP.items():
        assert lookup_key(canon) in _ALIAS_TO_CANONICAL, f"{canon} not canonical"
        assert _ALIAS_TO_CANONICAL[lookup_key(canon)][0] == canon
        for m in secondaries:
            assert canon in secondary_exercises(m), f"{canon} missing from reverse map for {m}"


def test_secondary_exercises_unknown_muscle_returns_empty():
    assert secondary_exercises(MuscleGroup.serratus) == []  # no entry credits serratus


def test_flat_bench_and_fly_are_not_core():
    # regression: neither name matched a keyword rule, so both fell to the
    # (key, core, True) fallback — flat bench was logged as core
    for raw in ("Bench Press", "Flat Bench", "Chest Press", "Fly", "Pec Deck"):
        name, mg, review = canonicalize(raw)
        assert mg is MuscleGroup.chest, f"{raw} -> {mg}"
        assert review is False, f"{raw} still needs review"


def test_flat_bench_credits_front_delt_and_triceps_not_side_delt():
    # ch03 horizontal push: chest + anterior delts primary, triceps secondary;
    # middle delts only on the INCLINE case
    for flat in ("Bench Press", "Barbell Bench Press", "Dumbbell Bench Press"):
        assert set(SECONDARY_OVERLAP[flat]) == {MuscleGroup.front_delt, MuscleGroup.triceps}
    assert set(SECONDARY_OVERLAP["Incline Bench Press"]) == {
        MuscleGroup.front_delt, MuscleGroup.triceps, MuscleGroup.side_delt,
    }


def test_fly_credits_front_delt_only():
    # ch03 fly: chest primary, anterior delts secondary — never side_delt
    for fly in ("Fly", "Dumbbell Fly", "Cable Fly", "Machine Fly"):
        assert SECONDARY_OVERLAP[fly] == [MuscleGroup.front_delt]


def test_lat_pulldown_matches_pull_up_overlap():
    # same ch03 row (vertical pull) as Pull-Up, so same credited muscles
    assert canonicalize("Lat Pulldown")[0] == "Lat Pulldown"
    assert SECONDARY_OVERLAP["Lat Pulldown"] == SECONDARY_OVERLAP["Pull-Up"]


def test_face_pull_does_not_resolve_to_lats():
    # "pull" keyword used to win and file it under lats
    name, mg, review = canonicalize("Face Pull")
    assert (name, mg, review) == ("Face Pull", MuscleGroup.rear_delt, False)
    assert canonicalize("Rope Face Pull")[1] is MuscleGroup.rear_delt


def test_reverse_fly_still_wins_over_the_fly_keyword():
    # "fly" was added to the keyword table; "reverse fly" must stay ahead of it
    assert canonicalize("Reverse Fly")[1] is MuscleGroup.rear_delt
    assert canonicalize("Machine Reverse Fly")[1] is MuscleGroup.rear_delt

