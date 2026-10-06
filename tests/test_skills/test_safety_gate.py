"""safety_gate unit tests — deterministic ban + alternatives + empty-table safe."""

from skills.init import get_duckdb
from skills.safety_gate import check_exercise_safety


def _seed_injury(loc="left_elbow", status="active", contra=None, alts=None):
    d = get_duckdb()
    d.execute(
        """
        INSERT INTO injury_status (location, status, severity,
                                    contraindicated_exercises, safe_alternatives)
        VALUES (?, ?, 5, ?, ?)
        """,
        [loc, status, contra or ["Skull Crusher"], alts or ["Tricep Pushdown"]],
    )


def test_empty_injury_table_is_safe():
    r = check_exercise_safety("Skull Crusher")
    assert r.safe is True
    assert r.alternatives == []


def test_contraindicated_exercise_is_unsafe_with_alternatives():
    _seed_injury(contra=["Skull Crusher"], alts=["Tricep Pushdown"])
    # a ban stored under the pre-split name "Skull Crusher" still covers the
    # dumbbell variant, which is now its own identity
    r = check_exercise_safety("Dumbbell Skull Crusher")
    assert r.safe is False
    assert r.exercise == "Dumbbell Skull Crusher"
    assert "Tricep Pushdown" in r.alternatives


def test_non_contraindicated_stays_safe_with_active_injury():
    _seed_injury(contra=["Skull Crusher"])
    r = check_exercise_safety("Incline Bench Press")
    assert r.safe is True


def test_resolved_injury_does_not_block():
    _seed_injury(loc="right_elbow", status="resolved", contra=["Skull Crusher"])
    r = check_exercise_safety("Skull Crusher")
    assert r.safe is True  # resolved -> ignored


def test_alias_canonicalized_before_match():
    _seed_injury(contra=["Skull Crusher"], alts=["Tricep Pushdown"])
    r = check_exercise_safety("Dumbbell Skull Crusher")
    assert r.safe is False  # alias matches canonical banned name


def test_lowercase_alias_still_blocked():
    # regression: "Dumbbell Skull Crusher" canonicalized and matched, but its
    # lowercase spelling missed the catalog, kept its raw name, and matched
    # nothing in the ban list -> safe=true for a banned exercise (F1 residual)
    _seed_injury(contra=["Skull Crusher"], alts=["Tricep Pushdown"])
    r = check_exercise_safety("dumbbell skull crusher")
    assert r.safe is False
    assert r.exercise == "Dumbbell Skull Crusher"
    assert "Tricep Pushdown" in r.alternatives


def test_ban_blocks_hyphen_and_space_spellings_both_ways():
    # P24: the catalog and the ban matcher normalize through one lookup_key,
    # so a hyphen-vs-space difference can never open a gap in either direction
    _seed_injury(contra=["Chest-Supported Dumbbell Row"], alts=["Machine Row"])
    for spelling in ("chest supported dumbbell row", "Chest-Supported Dumbbell Row",
                     "chest_supported  dumbbell-row", "chest supported db row"):
        assert check_exercise_safety(spelling).safe is False, spelling


def test_ban_stored_with_spaces_blocks_hyphenated_query():
    _seed_injury(contra=["chest supported dumbbell row"], alts=["Machine Row"])
    for spelling in ("Chest-Supported Dumbbell Row", "chest-supported db row"):
        assert check_exercise_safety(spelling).safe is False, spelling


def test_ban_on_unknown_name_ignores_hyphen_vs_space():
    # not in the catalog at all: the raw spellings still meet through lookup_key
    _seed_injury(contra=["Zercher-Squat Hold"], alts=["Leg Press"])
    assert check_exercise_safety("zercher squat hold").safe is False
