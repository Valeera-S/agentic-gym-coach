"""Text hygiene at every input boundary (P66, P70).

models/text.py's rules were applied to sessions and bodyweight only: a lone
surrogate crashed injuries_seed / memory_save / memory_search / profile_set /
safety_check (`internal`, a serialization error, or an `ok` that the MCP
framework then failed to send) and NUL was stored. Short identifiers allow no
control character; free text allows newline, carriage return and tab; a lone
surrogate is rejected everywhere; every failure is invalid_input.
"""

from __future__ import annotations

import json

import pytest

import mcp_server
from coach_tools import DISPATCH, error_payload
from skills.init import get_duckdb

SURROGATE = "\ud800"
NUL = "Foo\x00Bar"
BEL = "a\x07b"


def _invalid(cmd: str, args: dict):
    with pytest.raises(ValueError) as exc:
        DISPATCH[cmd](args)
    assert error_payload(exc.value)["error"] == "invalid_input"


def _count(table: str) -> int:
    return get_duckdb().execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# --- injuries_seed: ban and alternative names are single-line identifiers ----------

@pytest.mark.parametrize("bad", [SURROGATE, NUL, "Foo\nBar", "   "])
@pytest.mark.parametrize("field", ["contraindicated_exercises", "safe_alternatives"])
def test_injury_exercise_names_reject_surrogates_controls_and_blanks(field, bad):
    _invalid("injuries_seed", {"location": "left_knee", "status": "active", "severity": 3,
                               field: [bad]})
    assert _count("injury_status") == 0


# --- memory ------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [SURROGATE, "a\x00b", BEL])
def test_memory_text_rejects_surrogates_and_non_whitespace_controls(bad):
    _invalid("memory_save", {"text": bad})
    assert _count("memory_notes") == 0


def test_memory_text_keeps_newline_carriage_return_and_tab():
    out = DISPATCH["memory_save"]({"text": "line1\r\nline2\tend"})
    assert out["text"] == "line1\r\nline2\tend"


@pytest.mark.parametrize("bad", [SURROGATE, "a\x00b", "a\nb"])
def test_memory_tags_are_single_line_identifiers(bad):
    _invalid("memory_save", {"text": "ok", "tags": [bad]})
    _invalid("memory_search", {"tags": [bad]})
    assert _count("memory_notes") == 0


@pytest.mark.parametrize("bad", [SURROGATE, "a\x00b"])
def test_memory_search_query_is_checked(bad):
    _invalid("memory_search", {"query": bad})


# --- profile -----------------------------------------------------------------------

@pytest.mark.parametrize("bad", [SURROGATE, "a\x00b", "a\nb"])
def test_profile_short_strings_allow_no_control_characters(bad):
    _invalid("profile_set", {"display_name": bad})
    _invalid("profile_set", {"liked_exercises": [bad]})
    _invalid("profile_set", {"disliked_exercises": [bad]})
    _invalid("profile_set", {"concurrent_sports": [bad]})
    _invalid("profile_set", {"weekly_availability": [{"weekday": "mon", "venue": bad}]})
    assert _count("user_profiles") == 0


@pytest.mark.parametrize("field", ["supplement_notes", "caffeine_intake"])
@pytest.mark.parametrize("bad", [SURROGATE, "a\x00b", BEL])
def test_profile_free_text_rejects_surrogates_and_odd_controls(field, bad):
    _invalid("profile_set", {field: bad})


@pytest.mark.parametrize("bad", [SURROGATE, "a\x00b"])
def test_goal_metric_and_notes_are_free_text_checked(bad):
    _invalid("profile_set", {"goals": [{"kind": "strength", "metric": bad}]})
    _invalid("profile_set", {"goals": [{"kind": "strength", "notes": bad}]})


def test_profile_free_text_keeps_line_breaks():
    out = DISPATCH["profile_set"]({"supplement_notes": "creatine\nwhey\ttwice",
                                   "goals": [{"kind": "strength", "notes": "a\r\nb"}]})
    assert out["supplement_notes"] == "creatine\nwhey\ttwice"


def test_a_stored_legacy_profile_with_a_control_character_is_still_readable():
    from models import UserProfile
    from skills.profile import get_profile
    payload = UserProfile(display_name="ok").model_dump_json().replace('"ok"', '"a\\u0000b"')
    get_duckdb().execute("INSERT INTO user_profiles (updated_at, payload) VALUES (now(), ?)", [payload])
    assert get_profile().display_name == "a\x00b"


# --- safety_check (also P70) ---------------------------------------------------------

@pytest.mark.parametrize("bad", [SURROGATE, "Squat\x00", "", "   ", "\t\n"])
def test_safety_check_rejects_a_bad_or_blank_name(bad):
    _invalid("safety_check", {"exercise": bad})


@pytest.mark.parametrize("bad", [SURROGATE, "   "])
def test_safety_check_over_mcp_returns_the_error_dict(bad):
    out = mcp_server.coach_safety_check(bad)
    assert out["error"] == "invalid_input"
    json.dumps(out).encode("utf-8")  # a client can send it


def test_error_detail_never_carries_a_lone_surrogate():
    with pytest.raises(ValueError) as exc:
        DISPATCH["memory_save"]({"text": "ok", "kind": SURROGATE})
    payload = error_payload(exc.value)
    assert payload["error"] == "invalid_input"
    payload["detail"].encode("utf-8")
