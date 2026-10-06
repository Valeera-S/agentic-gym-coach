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


# --- P33: the gate never offers an alternative that is itself banned --------

def test_generic_ban_alternatives_that_are_variants_are_not_offered():
    # ban on generic "Fly": Cable Fly / Machine Fly are variants -> banned too
    _seed_injury(contra=["Fly"], alts=["Dip", "Pec Deck", "Cable Fly"])  # Pec Deck = Machine Fly
    r = check_exercise_safety("Dumbbell Fly")
    assert r.safe is False
    assert "Cable Fly" not in r.alternatives and "Machine Fly" not in r.alternatives
    assert r.alternatives == ["Dip"]
    for alt in r.alternatives:
        assert check_exercise_safety(alt).safe is True


def test_alternative_banned_by_another_row_is_dropped_and_others_merged():
    _seed_injury(loc="left_shoulder", contra=["Barbell Bench Press"],
                 alts=["Dumbbell Bench Press", "Dip"])
    _seed_injury(loc="right_shoulder", contra=["Dumbbell Bench Press", "Barbell Bench Press"],
                 alts=["Cable Crossover"])
    r = check_exercise_safety("Barbell Bench Press")
    assert r.safe is False
    assert "Dumbbell Bench Press" not in r.alternatives
    # merged from every matching row, de-duplicated, deterministic order
    assert r.alternatives == ["Cable Crossover", "Dip"]  # newest row first
    assert check_exercise_safety("Barbell Bench Press").alternatives == r.alternatives


def test_no_surviving_alternative_gives_empty_list_and_message():
    _seed_injury(contra=["Fly"], alts=["Cable Fly"])
    r = check_exercise_safety("Dumbbell Fly")
    assert r.safe is False
    assert r.alternatives == []
    assert "no safe alternative" in r.message.lower()


def test_safe_result_has_no_message():
    assert check_exercise_safety("Squat").message == ""


# --- P44: Unicode-aware name normalization (one lookup_key owner) -----------

_UNICODE_SPELLINGS = (
    "Ｄｕｍｂｂｅｌｌ Ｆｌｙ",        # fullwidth
    "Dumbbell\u200bFly",              # zero-width space
    "Dumbbell\u00a0Fly",              # no-break space
    "Dumbbell Fly.",                  # trailing punctuation
    "DUMBBELL\u2009FLY",              # thin space + case
)


def test_unicode_spellings_of_a_banned_name_are_blocked():
    _seed_injury(contra=["Dumbbell Fly"], alts=["Push-Up"])
    for spelling in _UNICODE_SPELLINGS:
        r = check_exercise_safety(spelling)
        assert r.safe is False, repr(spelling)
        assert r.exercise == "Dumbbell Fly"


def test_ban_stored_with_unicode_spelling_blocks_plain_query():
    _seed_injury(contra=["Ｄｕｍｂｂｅｌｌ Ｆｌｙ"], alts=["Push-Up"])
    assert check_exercise_safety("Dumbbell Fly").safe is False
    _seed_injury(loc="right_elbow", contra=["Zercher\u200bHold."], alts=["Push-Up"])
    assert check_exercise_safety("zercher hold").safe is False


def test_lookup_key_rules():
    from models.exercise_catalog import lookup_key
    assert lookup_key("Ｄｕｍｂｂｅｌｌ  Ｆｌｙ") == "dumbbell fly"
    assert lookup_key("Dumbbell\u200bFly") == "dumbbell fly"  # zero-width SPACE is a word break
    assert lookup_key("Dumb\u00adbell\u200dFly") == "dumbbellfly"  # soft hyphen / ZWJ: dropped
    assert lookup_key("  Chest-Supported_Row. ") == "chest supported row"
    assert lookup_key("ß") == "ss"  # casefold, not lower
    assert lookup_key("深蹲") == "深蹲"  # non-Latin names survive
    assert lookup_key("...") == ""


# --- P76: an unrecognized name fails closed while any ban is active ---------

def test_unrecognized_non_english_name_is_unsafe_when_a_ban_is_active():
    # the real failure: a ban on Bench Press, the user says it in Chinese
    _seed_injury(contra=["Bench Press"], alts=["Push-Up"])
    r = check_exercise_safety("卧推")
    assert r.safe is False
    assert r.alternatives == []
    assert "not recognized" in r.reason.lower()
    assert "active" in r.reason.lower()
    assert "catalog" in r.message.lower() and "english" in r.message.lower()
    assert "do not suggest" in r.message.lower()


def test_typo_of_a_banned_name_is_unsafe_when_a_ban_is_active():
    _seed_injury(contra=["Bench Press"], alts=["Push-Up"])
    r = check_exercise_safety("Bench Pres")
    assert r.safe is False
    assert r.alternatives == []
    assert "not recognized" in r.reason.lower()


def test_unrecognized_name_with_no_injuries_stays_safe_with_review_suffix():
    r = check_exercise_safety("卧推")
    assert r.safe is True
    assert "not in the catalog" in r.reason


def test_unrecognized_name_with_only_resolved_injuries_stays_safe():
    _seed_injury(loc="left_elbow", status="resolved", contra=["Bench Press"])
    r = check_exercise_safety("卧推")
    assert r.safe is True
    assert "not in the catalog" in r.reason


def test_unrecognized_name_after_latest_row_resolves_stays_safe():
    _seed_injury(loc="left_elbow", status="active", contra=["Bench Press"])
    _seed_injury(loc="left_elbow", status="resolved", contra=["Bench Press"])
    assert check_exercise_safety("卧推").safe is True


def test_active_injury_without_any_ban_does_not_trip_the_unrecognized_rule():
    d = get_duckdb()
    d.execute("INSERT INTO injury_status (location, status, severity, contraindicated_exercises,"
              " safe_alternatives) VALUES ('left_elbow', 'active', 3, [], [])")
    assert check_exercise_safety("卧推").safe is True


def test_literal_ban_on_an_unrecognized_name_still_matches_with_its_alternatives():
    _seed_injury(contra=["卧推"], alts=["Dip"])
    r = check_exercise_safety("卧推")
    assert r.safe is False
    assert r.alternatives == ["Dip"]
    assert "not recognized" not in r.reason.lower()


def test_recognized_names_are_unchanged_with_active_bans():
    _seed_injury(contra=["Skull Crusher"], alts=["Tricep Pushdown"])
    r = check_exercise_safety("Squat")
    assert r.safe is True and r.reason == "no active contraindication" and r.message == ""


def test_unrecognized_alternative_is_not_offered_while_bans_are_active():
    # an offered alternative must pass the gate itself; an unknown name cannot
    _seed_injury(contra=["Skull Crusher"], alts=["Tricep Pushdown", "Zzyzx Curl"])
    r = check_exercise_safety("Skull Crusher")
    assert r.safe is False
    assert "Zzyzx Curl" not in r.alternatives
    for alt in r.alternatives:
        assert check_exercise_safety(alt).safe is True
