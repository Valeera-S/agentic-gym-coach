"""Strict typing for injuries and the profile (P67).

Sessions and bodyweight already refused `true` as a number and "80" as a
number (P41); injuries_seed and profile_set stored `severity: true` as 1,
`bodyweight_kg: true` as 1.0 kg, `rpe_calibrated: "false"` as false and
`age_years: "30"` as 30 -- and the intake then counted them as collected.
"""

from __future__ import annotations

import pytest

from coach_tools import DISPATCH, error_payload
from skills.init import get_duckdb


def _invalid(cmd: str, args: dict):
    with pytest.raises(ValueError) as exc:
        DISPATCH[cmd](args)
    assert error_payload(exc.value)["error"] == "invalid_input"


def _count(table: str) -> int:
    return get_duckdb().execute(f"SELECT count(*) FROM {table}").fetchone()[0]


@pytest.mark.parametrize("bad", [True, False, "3", 3.0, 3.5, None])
def test_injury_severity_must_be_a_real_integer(bad):
    with pytest.raises((ValueError, TypeError)) as exc:
        DISPATCH["injuries_seed"]({"location": "left_knee", "status": "active", "severity": bad})
    assert error_payload(exc.value)["error"] == "invalid_input"
    assert _count("injury_status") == 0


def test_injury_severity_integer_still_accepted():
    out = DISPATCH["injuries_seed"]({"location": "left_knee", "status": "active", "severity": 3})
    assert out["injury"]["severity"] == 3


@pytest.mark.parametrize("field, bad", [
    ("days_per_week", True), ("days_per_week", "3"), ("days_per_week", 3.0),
    ("session_length_min", True), ("session_length_min", "60"), ("session_length_min", 60.0),
    ("age_years", True), ("age_years", "30"), ("age_years", 30.0),
    ("bodyweight_kg", True), ("bodyweight_kg", "80"),
    ("bodyfat_pct", True), ("bodyfat_pct", "15"),
    ("diet_phase_duration_weeks", True), ("diet_phase_duration_weeks", "4"),
    ("eating_out_per_week", True), ("eating_out_per_week", 2.0),
    ("alcohol_per_week", True), ("alcohol_per_week", "2"),
    ("rpe_calibrated", "false"), ("rpe_calibrated", 0), ("rpe_calibrated", "true"),
    ("has_tested_maxes", 1), ("has_tested_maxes", "yes"),
    ("family_diabetes_history", "false"), ("pcos", 0), ("oligomenorrhea", "no"),
])
def test_profile_numbers_and_booleans_are_strict(field, bad):
    _invalid("profile_set", {field: bad})
    assert _count("user_profiles") == 0


def test_profile_int_is_still_fine_for_a_float_field_and_real_types_pass():
    out = DISPATCH["profile_set"]({"bodyweight_kg": 80, "bodyfat_pct": 15.5, "days_per_week": 4,
                                   "age_years": 30, "rpe_calibrated": False, "pcos": True})
    assert out["bodyweight_kg"] == 80.0 and out["rpe_calibrated"] is False and out["pcos"] is True


@pytest.mark.parametrize("field", ["liked_exercises", "disliked_exercises", "concurrent_sports"])
@pytest.mark.parametrize("bad", ["", "   "])
def test_profile_string_lists_reject_blank_items(field, bad):
    _invalid("profile_set", {field: [bad]})
    _invalid("profile_set", {field: ["Squat", bad]})
    assert _count("user_profiles") == 0


def test_a_stored_legacy_profile_with_blank_items_is_still_readable():
    from models import UserProfile
    from skills.profile import get_profile
    payload = UserProfile().model_copy(update={"liked_exercises": [""]}).model_dump_json()
    get_duckdb().execute("INSERT INTO user_profiles (updated_at, payload) VALUES (now(), ?)", [payload])
    assert get_profile().liked_exercises == [""]
