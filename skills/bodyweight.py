"""bodyweight — the bodyweight time series (table `bodyweight_log`).

Nutrition ch02: every calorie number is a hypothesis, corrected only by
WEEKLY-AVERAGE bodyweight trends. That needs a series, and the series is only
meaningful like-for-like: a post-workout reading and a fasted morning reading
differ by more than a week of real change. So every reading carries a
condition, and `summarize` averages PER CONDITION — it never blends them — over
DAILY values (readings within a day are averaged first).

Does not write profile.bodyweight_kg (profile writes need the user's confirmation).
"""

from __future__ import annotations

from datetime import date

from models.bodyweight import (
    BodyweightHistory, BodyweightInput, BodyweightReading, BodyweightSummary,
    ConditionAverage, WeighCondition,
)
from models.enums import WeightUnit

from .init import get_duckdb
from .trend_analysis import window_start

_COLS = "id, date, weight_kg, weight_entered, weight_unit, condition, scale, notes, created_at"


def _reading(r) -> BodyweightReading:
    return BodyweightReading(
        id=r[0], date=r[1], weight_kg=r[2], weight_entered=r[3],
        weight_unit=WeightUnit(r[4]) if r[4] else None,
        condition=WeighCondition(r[5]), scale=r[6], notes=r[7], created_at=r[8])


def log_bodyweight(inp: BodyweightInput) -> BodyweightReading:
    """Persist one reading (already validated by BodyweightInput)."""
    row = get_duckdb().execute(
        f"""INSERT INTO bodyweight_log
              (date, weight_kg, weight_entered, weight_unit, condition, scale, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING {_COLS}""",
        [inp.date, inp.weight_kg, inp.weight, inp.unit.value if inp.unit else None,
         inp.condition.value, inp.scale, inp.notes],
    ).fetchone()
    return _reading(row)


def list_readings(start: date, end: date) -> list[BodyweightReading]:
    """Readings with start <= date <= end, oldest first."""
    rows = get_duckdb().execute(
        f"SELECT {_COLS} FROM bodyweight_log WHERE date BETWEEN ? AND ? "
        "ORDER BY date, created_at, id", [start, end]).fetchall()
    return [_reading(r) for r in rows]


def summarize(start: date, end: date) -> BodyweightSummary:
    """Mean kg per condition over start..end inclusive, plus how many readings
    and how many distinct days stand behind it.

    The mean is of DAILY values: readings within one day are averaged first,
    then the days (Nutrition ch02 — weekly averages of daily weights), so two
    readings on one day weigh as one day, not two."""
    rows = get_duckdb().execute(
        "SELECT condition, sum(n), count(*), avg(day_mean) FROM ("
        "  SELECT condition, date, count(*) AS n, avg(weight_kg) AS day_mean "
        "  FROM bodyweight_log WHERE date BETWEEN ? AND ? GROUP BY condition, date) "
        "GROUP BY condition ORDER BY condition",
        [start, end]).fetchall()
    return BodyweightSummary(start=start, end=end, averages=[
        ConditionAverage(condition=WeighCondition(c), readings=int(n), days=int(d),
                              mean_kg=round(float(m), 2))
        for c, n, d, m in rows])


def fasted_mean_7d(end: date) -> float | None:
    """7-day mean of the daily morning_fasted values ending at `end`, or None."""
    for a in summarize(window_start(end, 7), end).averages:
        if a.condition is WeighCondition.morning_fasted:
            return a.mean_kg
    return None


def bodyweight_history(window_days: int, end: date) -> BodyweightHistory:
    start = window_start(end, window_days)
    return BodyweightHistory(
        window_days=window_days, start=start, end=end,
        readings=list_readings(start, end),
        last_7_days=summarize(window_start(end, 7), end),
        window=summarize(start, end))
