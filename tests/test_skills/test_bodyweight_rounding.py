"""P64b: kg values in bodyweight READ outputs are rounded to 2 decimals; stored
values and the entered value + unit stay exact."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from models.bodyweight import BodyweightInput
from skills.bodyweight import bodyweight_history, fasted_mean_7d, list_readings, log_bodyweight
from skills.init import get_duckdb
from skills.snapshot import generate_phase_snapshot

TODAY = date.today()


def _log_lb(days_ago: int, lb: float, cond: str = "morning_fasted"):
    return log_bodyweight(BodyweightInput(
        date=TODAY - timedelta(days=days_ago), weight=lb, unit="lb", condition=cond))


def test_lb_readings_come_back_rounded_to_two_decimals():
    _log_lb(2, 145.5)   # 66.0000000000...: unrounded, an ugly float
    _log_lb(1, 144.7)   # 65.63481593899999
    h = bodyweight_history(7, TODAY)
    kgs = sorted(r.weight_kg for r in h.readings)
    assert kgs == [65.63, 66.0]
    assert all(round(k, 2) == k for k in kgs)
    assert [r.weight_kg for r in list_readings(TODAY - timedelta(days=7), TODAY)] == [66.0, 65.63]


def test_means_are_two_decimals_and_entered_value_and_unit_stay_exact():
    _log_lb(2, 145.5)
    _log_lb(1, 144.7)
    h = bodyweight_history(7, TODAY)
    (avg,) = h.last_7_days.averages
    assert round(avg.mean_kg, 2) == avg.mean_kg == pytest.approx(65.82, abs=0.005)
    assert sorted((r.weight_entered, r.weight_unit.value) for r in h.readings) == [
        (144.7, "lb"), (145.5, "lb")]
    stored = get_duckdb().execute(
        "SELECT weight_kg FROM bodyweight_log WHERE weight_entered = 144.7").fetchone()[0]
    assert stored == 144.7 * 0.45359237  # the stored value is not rounded


def test_snapshot_body_weight_is_two_decimals():
    _log_lb(2, 145.5)
    _log_lb(1, 144.7)
    kg = generate_phase_snapshot().body_weight_kg
    assert kg == fasted_mean_7d(TODAY) and round(kg, 2) == kg
