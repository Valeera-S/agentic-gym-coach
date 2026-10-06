"""Plausibility window for dates a caller supplies (P38).

A session or bodyweight reading dated 2099-01-01 made weeks_since_last_session
negative and silenced the reassessment signal forever; 0001-01-01 is as
meaningless. Input dates must lie in EARLIEST..today. There is no slack for time
zones: the app runs locally, and a session dated tomorrow made weeks_since_last_session
and block_state negative and sat outside every window ending today (P69). The same
bound applies to the read tools' date arguments. Input boundary only: readers keep
tolerating whatever is stored.

`today()` is the single clock; tests monkeypatch `models.dates.today`.
"""

from __future__ import annotations

from datetime import date

EARLIEST = date(2000, 1, 1)


def today() -> date:
    return date.today()


def check_plausible_date(d: date) -> date:
    latest = today()
    if not EARLIEST <= d <= latest:
        raise ValueError(
            f"date {d.isoformat()} is outside the plausible range "
            f"{EARLIEST.isoformat()}..{latest.isoformat()} (today); "
            "check the year")
    return d
