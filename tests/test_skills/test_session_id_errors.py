"""A malformed session id is invalid_input with a clear message (P63).

It used to surface Python's "badly formed hexadecimal UUID string".
"""

from __future__ import annotations

import pytest

from coach_tools import DISPATCH, error_payload

BAD_IDS = ["nope", "", "123", "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz", "1" * 40]
AMEND = {"date": "2026-10-01", "exercises": [{"new": True, "name": "Squat", "sets": 1}]}


@pytest.mark.parametrize("bad", BAD_IDS)
@pytest.mark.parametrize("cmd, extra", [
    ("session_detail", {}),
    ("session_delete", {}),
    ("session_amend", AMEND),
])
def test_a_malformed_session_id_says_what_a_session_id_is(cmd, extra, bad):
    with pytest.raises(ValueError, match=r"session_id must be a UUID \(as returned by coach_sessions") as exc:
        DISPATCH[cmd]({"session_id": bad, **extra})
    assert error_payload(exc.value)["error"] == "invalid_input"
    assert "hexadecimal" not in str(exc.value)


@pytest.mark.parametrize("cmd, extra", [
    ("session_detail", {}), ("session_delete", {}), ("session_amend", AMEND)])
def test_a_well_formed_but_unknown_session_id_is_still_a_plain_not_found(cmd, extra):
    with pytest.raises(ValueError, match="no session with id") as exc:
        DISPATCH[cmd]({"session_id": "00000000-0000-0000-0000-000000000001", **extra})
    assert error_payload(exc.value)["error"] == "invalid_input"
