"""coach_profile_set merges (P71).

It used to replace the whole profile, so a partial dict silently wiped answered
fields (concurrent_sports: [] included). Now keys present overwrite, keys absent
keep the stored value, an explicit null clears that field, and `goals` when
present replaces the whole goal list (audited exactly as before).
"""

from __future__ import annotations

import pytest

from coach_tools import DISPATCH
from skills.init import get_duckdb

SET = DISPATCH["profile_set"]
GET = DISPATCH["profile_get"]


def _goal_audits() -> list[str]:
    return [r[0] for r in get_duckdb().execute(
        "SELECT trigger_signal FROM decision_log WHERE event_type = 'goal_change' "
        "ORDER BY created_at").fetchall()]


def _rows() -> int:
    return get_duckdb().execute("SELECT count(*) FROM user_profiles").fetchone()[0]


def test_absent_keys_keep_their_stored_values_and_present_keys_overwrite():
    SET({"display_name": "Ann", "days_per_week": 4, "concurrent_sports": [],
         "liked_exercises": ["Squat"], "rpe_calibrated": False})
    out = SET({"display_name": "Bea"})
    assert out["display_name"] == "Bea"
    assert out["days_per_week"] == 4
    assert out["concurrent_sports"] == []          # an answered "none" is not wiped
    assert out["liked_exercises"] == ["Squat"]
    assert out["rpe_calibrated"] is False
    assert GET({}) ["days_per_week"] == 4


def test_a_list_field_that_is_present_replaces_the_stored_list():
    SET({"liked_exercises": ["Squat", "Row"], "days_per_week": 3})
    out = SET({"liked_exercises": ["Dip"]})
    assert out["liked_exercises"] == ["Dip"] and out["days_per_week"] == 3


def test_an_explicit_null_clears_that_field_only():
    SET({"display_name": "Ann", "days_per_week": 4, "age_years": 30, "concurrent_sports": ["judo"]})
    out = SET({"age_years": None, "concurrent_sports": None})
    assert out["age_years"] is None
    assert out["concurrent_sports"] is None        # never asked again, like a fresh profile
    assert out["display_name"] == "Ann" and out["days_per_week"] == 4


def test_a_null_list_field_clears_to_empty():
    SET({"liked_exercises": ["Squat"], "days_per_week": 3})
    assert SET({"liked_exercises": None})["liked_exercises"] == []


def test_goals_present_replace_the_whole_list_and_are_audited_as_before():
    SET({"goals": [{"kind": "strength"}], "days_per_week": 3})
    assert _goal_audits() == []                    # the first profile has no prior goals
    out = SET({"goals": [{"kind": "hypertrophy", "target_muscles": ["chest"]}]})
    assert [g["kind"] for g in out["goals"]] == ["hypertrophy"]
    audits = _goal_audits()
    assert len(audits) == 1 and "strength" in audits[0] and "hypertrophy" in audits[0]


def test_goals_absent_are_kept_and_write_no_goal_audit():
    SET({"goals": [{"kind": "strength"}], "days_per_week": 3})
    out = SET({"days_per_week": 5})
    assert [g["kind"] for g in out["goals"]] == ["strength"]
    assert _goal_audits() == []


def test_goals_null_clears_them_with_an_audit_entry():
    SET({"goals": [{"kind": "strength"}], "days_per_week": 3})
    out = SET({"goals": None})
    assert out["goals"] == []
    assert len(_goal_audits()) == 1


def test_the_get_then_set_round_trip_still_works():
    SET({"display_name": "Ann", "goals": [{"kind": "strength", "metric": "2x BW squat"}],
         "days_per_week": 4, "bodyweight_kg": 80, "concurrent_sports": []})
    got = GET({})
    out = SET(got)
    out.pop("updated_at"); got.pop("updated_at")
    assert out == got
    assert _goal_audits() == []


def test_the_first_profile_behaves_as_before():
    out = SET({"days_per_week": 4})
    assert out["days_per_week"] == 4 and out["goals"] == []
    assert _rows() == 1


def test_an_empty_or_all_cleared_profile_is_still_refused_and_nothing_is_written():
    with pytest.raises(ValueError, match="empty"):
        SET({})
    SET({"display_name": "Ann"})
    with pytest.raises(ValueError, match="empty"):
        SET({"display_name": None})
    with pytest.raises(ValueError, match="empty"):
        SET({})
    assert _rows() == 1
    assert GET({})["display_name"] == "Ann"


def test_a_merge_is_validated_and_a_rejected_one_writes_nothing():
    SET({"display_name": "Ann", "days_per_week": 4})
    for bad in ({"days_per_week": True}, {"display_name": "a\x00b"}, {"days_per_week": 9},
                {"unknown_field": 1}):
        with pytest.raises(ValueError):
            SET(bad)
    assert _rows() == 1 and GET({})["days_per_week"] == 4


def test_a_legacy_stored_value_does_not_block_editing_another_field():
    from models import UserProfile
    payload = UserProfile(display_name="ok").model_dump_json().replace('"ok"', '"a\\u0000b"')
    get_duckdb().execute("INSERT INTO user_profiles (updated_at, payload) VALUES (now(), ?)", [payload])
    out = SET({"days_per_week": 3})
    assert out["days_per_week"] == 3 and out["display_name"] == "a\x00b"


def test_the_mcp_docstring_and_prompt_describe_merge_semantics():
    import mcp_server
    from pathlib import Path
    doc = mcp_server.coach_profile_set.__doc__
    assert "Create/update" not in doc and "null clears" in doc
    prompt = (Path(__file__).resolve().parent.parent / "docs" / "COACH_PROMPT.md").read_text(encoding="utf-8")
    assert "send only the fields to change" in prompt and "null clears" in prompt
