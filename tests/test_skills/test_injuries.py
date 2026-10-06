"""injuries skill — the validated, canonicalizing injury_status boundary.

Covers the audit's P1 fix (validate + canonicalize at the write) and the
adversarial findings F1/F2/F9 (fail-open gate, garbage vocab, deferred
Tier-1 crash).
"""

import pytest
from pydantic import ValidationError

import orchestrator
from models import InjurySeedResult, InjuryStatus
from skills.init import get_duckdb
from skills.injuries import (
    get_active_injuries,
    list_injuries,
    seed_injury,
    tendon_summary,
)
from skills.safety_gate import check_exercise_safety


def _injury_rows() -> int:
    return get_duckdb().execute("SELECT count(*) FROM injury_status").fetchone()[0]


def test_seed_validates_location_vocabulary_and_writes_nothing_on_error():
    with pytest.raises(ValidationError):
        seed_injury("elbow", "active", 3)  # not a PainLocation (F2)
    assert _injury_rows() == 0


def test_seed_validates_status_and_severity_bounds():
    with pytest.raises(ValidationError):
        seed_injury("left_elbow", "banana", 3)  # not an InjuryState (F2)
    with pytest.raises(ValidationError):
        seed_injury("left_elbow", "active", 3.7)  # fractional severity (F2)
    with pytest.raises(ValidationError):
        seed_injury("left_elbow", "active", 11)  # DB CHECK previously the only guard
    assert _injury_rows() == 0


def test_seed_canonicalizes_alias_and_gate_blocks_canonical_query():
    res = seed_injury("left_elbow", "active", 4,
                      contraindicated_exercises=["Dumbbell Skull Crusher"],
                      safe_alternatives=["Tricep Pushdown"])
    assert isinstance(res, InjurySeedResult)
    assert res.injury.contraindicated_exercises == ["Dumbbell Skull Crusher"]
    assert res.needs_review == []
    r = check_exercise_safety("Skull Crusher")  # generic name covers the banned variant
    assert r.safe is False
    assert "Tricep Pushdown" in r.alternatives


def test_seed_unmapped_name_stored_and_flagged_needs_review():
    # The F1 residual: "skullcrushers" is not an alias. It is stored verbatim
    # and flagged so the coach confirms it with the user.
    res = seed_injury("left_elbow", "active", 4,
                      contraindicated_exercises=["skullcrushers"])
    assert res.needs_review == ["skullcrushers"]


def test_gate_matches_stored_names_case_insensitively():
    # Ban written as the user said it (lowercase) still blocks the canonical
    # query — the gate normalizes case/whitespace on both sides.
    seed_injury("right_elbow", "active", 3,
                contraindicated_exercises=["skull crusher"])
    r = check_exercise_safety("Skull Crusher")
    assert r.safe is False


def test_gate_flags_unmapped_query_name_in_reason():
    r = check_exercise_safety("zzz unknown machine")
    assert r.safe is True
    assert "not in the catalog" in r.reason


def test_no_deferred_crash_after_valid_seed():
    # F9 chain: a seeded row must never poison a later Tier-1 load.
    seed_injury("left_elbow", "active", 2)
    wm = orchestrator.initialize_session()
    assert len(wm.active_injuries) == 1
    assert wm.active_injuries[0].location.value == "left_elbow"


def test_list_injuries_returns_models_newest_first():
    seed_injury("left_elbow", "active", 2)
    seed_injury("right_knee", "resolved", 1)
    rows = list_injuries()
    assert all(isinstance(r, InjuryStatus) for r in rows)
    assert {r.location.value for r in rows} == {"left_elbow", "right_knee"}
    assert rows[0].id is not None
    active = list_injuries(active_only=True)
    assert {r.location.value for r in active} == {"left_elbow"}


def test_tendon_summary_shape():
    seed_injury("left_elbow", "active", 5)
    seed_injury("lower_back", "resolved", 1)
    summary = tendon_summary()
    assert summary == {"left_elbow": {"status": "active", "severity": 5}}


# --- P34: the latest row per location is the current state ------------------

def test_resolved_row_lifts_earlier_active_ban():
    seed_injury("left_knee", "active", 6, contraindicated_exercises=["Squat"])
    assert check_exercise_safety("Squat").safe is False
    seed_injury("left_knee", "resolved", 0)
    assert check_exercise_safety("Squat").safe is True
    assert "left_knee" not in tendon_summary()
    assert get_active_injuries() == []


def test_other_locations_keep_their_bans_when_one_resolves():
    seed_injury("left_knee", "active", 6, contraindicated_exercises=["Squat"])
    seed_injury("lower_back", "active", 4, contraindicated_exercises=["Squat"])
    seed_injury("left_knee", "resolved", 0)
    assert check_exercise_safety("Squat").safe is False  # lower_back still bans it


def test_reactivation_after_resolved_blocks_again():
    seed_injury("left_knee", "active", 6, contraindicated_exercises=["Squat"])
    seed_injury("left_knee", "resolved", 0)
    seed_injury("left_knee", "resolving", 2, contraindicated_exercises=["Leg Press"])
    assert check_exercise_safety("Squat").safe is True  # old row is history
    assert check_exercise_safety("Leg Press").safe is False
    assert tendon_summary() == {"left_knee": {"status": "resolving", "severity": 2}}


def test_tendon_summary_reports_latest_row_per_location_deterministically():
    seed_injury("left_elbow", "active", 7)
    seed_injury("left_elbow", "chronic_baseline", 2)
    for _ in range(5):
        assert tendon_summary() == {"left_elbow": {"status": "chronic_baseline", "severity": 2}}


def test_list_injuries_keeps_history_and_flags_current():
    seed_injury("left_knee", "active", 6, contraindicated_exercises=["Squat"])
    seed_injury("left_knee", "resolved", 0)
    seed_injury("lower_back", "active", 3)
    rows = list_injuries()
    assert len(rows) == 3
    cur = {(r.location.value, r.status.value): r.is_current for r in rows}
    assert cur == {("left_knee", "active"): False, ("left_knee", "resolved"): True,
                   ("lower_back", "active"): True}
    assert [r.location.value for r in get_active_injuries()] == ["lower_back"]


# --- P35: ban/alternative lists must be lists of non-blank strings ----------

@pytest.mark.parametrize("field", ["contraindicated_exercises", "safe_alternatives"])
@pytest.mark.parametrize("bad", ["Leg Press", [""], ["  "], ["Squat", None], [3], 5])
def test_seed_rejects_non_list_or_blank_names_and_writes_nothing(field, bad):
    with pytest.raises(ValueError):
        seed_injury("left_knee", "active", 4, **{field: bad})
    assert _injury_rows() == 0


# --- P33: seed rejects alternatives the row's own bans would block ----------

def test_seed_rejects_alternative_blocked_by_own_ban_naming_it():
    with pytest.raises(ValueError, match="Cable Fly"):
        seed_injury("left_shoulder", "active", 4,
                    contraindicated_exercises=["Fly"],
                    safe_alternatives=["Pec Deck", "Cable Fly"])
    assert _injury_rows() == 0


def test_seed_rejects_alternative_equal_via_alias_or_generic():
    with pytest.raises(ValueError, match="Dumbbell Fly"):
        seed_injury("left_shoulder", "active", 4,
                    contraindicated_exercises=["Dumbbell Fly"],
                    safe_alternatives=["dumbbell fly"])
    with pytest.raises(ValueError, match="Fly"):  # generic alt covers a banned variant
        seed_injury("left_shoulder", "active", 4,
                    contraindicated_exercises=["Dumbbell Fly"],
                    safe_alternatives=["Fly"])
    assert _injury_rows() == 0
