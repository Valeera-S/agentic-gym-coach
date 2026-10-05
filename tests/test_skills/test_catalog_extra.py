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
    assert canonicalize("Overhead Press") == ("Shoulder Press", MuscleGroup.side_delt, False)


def test_alias_hits_are_case_insensitive():
    # casing must not decide whether a catalog name maps: an unmapped name
    # keeps its raw spelling, which the safety gate then fails to match
    for raw in ("overhead press", "OVERHEAD PRESS", "  Overhead Press  "):
        assert canonicalize(raw) == ("Shoulder Press", MuscleGroup.side_delt, False)


def test_case_variant_keeps_its_secondary_overlap():
    # "incline bench press" used to miss the table, so trend_analysis credited
    # no secondaries and under-counted triceps/side_delt hard sets
    name, _mg, review = canonicalize("incline bench press")
    assert (name, review) == ("Incline Bench Press", False)
    assert SECONDARY_OVERLAP[name] == [MuscleGroup.triceps, MuscleGroup.side_delt]


def test_keyword_fallback_returns_raw_name_with_review():
    name, mg, review = canonicalize("Meadows Row")
    assert (name, mg, review) == ("Meadows Row", MuscleGroup.mid_back, True)


def test_unknown_exercise_defaults_to_core_and_review():
    name, mg, review = canonicalize("Zercher Carry")
    assert review is True
    assert mg == MuscleGroup.core
    assert name == "Zercher Carry"


def test_empty_string_is_unknown_core():
    name, mg, review = canonicalize("")
    assert (name, mg, review) == ("", MuscleGroup.core, True)


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


def test_flat_bench_credits_triceps_only():
    # ch03 puts middle delts on the INCLINE case, not on flat bench
    assert SECONDARY_OVERLAP["Bench Press"] == [MuscleGroup.triceps]
    assert SECONDARY_OVERLAP["Incline Bench Press"] == [
        MuscleGroup.triceps, MuscleGroup.side_delt,
    ]


def test_fly_claims_no_secondary():
    # the chart's secondary for Fly is "Anterior delts", which MuscleGroup
    # cannot express; borrowing side_delt would invent a citation
    assert SECONDARY_OVERLAP["Fly"] == []


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

