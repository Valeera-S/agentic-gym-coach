"""Bodyweight-log Pydantic models (migration 0005, table `bodyweight_log`)."""

from __future__ import annotations

import math
from datetime import date as Date, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import WeightUnit
from .session import LB_TO_KG, StrictNum

MAX_BODYWEIGHT_KG = 400.0


class WeighCondition(str, Enum):
    """Under what conditions the scale reading was taken. Required on every
    reading: only like-for-like readings may be averaged together (Nutrition
    ch02). `unknown` is for a user who truly does not know."""
    morning_fasted = "morning_fasted"
    fed = "fed"
    post_workout = "post_workout"
    unknown = "unknown"


class BodyweightInput(BaseModel):
    """One reading. Send `weight` + `unit` ('kg' | 'lb') OR `weight_kg`, never
    both disagreeing (same rule as session exercises); 1 lb = 0.45359237 kg."""
    model_config = ConfigDict(extra="forbid")

    date: Date
    condition: WeighCondition
    weight: StrictNum | None = None  # a JSON number: never `true`, never "80" (P41)
    unit: WeightUnit | None = None
    weight_kg: StrictNum | None = None
    scale: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _to_kg(self) -> "BodyweightInput":
        if self.weight is None and self.unit is not None:
            raise ValueError("unit applies to `weight`, which is missing")
        if self.weight is not None:
            if self.unit is None:
                raise ValueError("weight needs a unit: 'kg' or 'lb'")
            kg = self.weight * (LB_TO_KG if self.unit is WeightUnit.lb else 1.0)
            if self.weight_kg is not None and not math.isclose(
                    self.weight_kg, kg, rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError("send either weight_kg or weight + unit, not both "
                                 "(the two given here disagree)")
            self.weight_kg = kg
        if self.weight_kg is None:
            raise ValueError("send weight + unit, or weight_kg")
        if not math.isfinite(self.weight_kg) or not 0 < self.weight_kg <= MAX_BODYWEIGHT_KG:
            raise ValueError(f"bodyweight must be > 0 and <= {MAX_BODYWEIGHT_KG:g} kg "
                             f"(got {self.weight_kg:g} kg)")
        return self


class BodyweightReading(BaseModel):
    id: UUID
    date: Date
    weight_kg: float
    weight_entered: float | None = None
    weight_unit: WeightUnit | None = None
    condition: WeighCondition
    scale: str | None = None
    notes: str | None = None
    created_at: datetime | None = None


class ConditionAverage(BaseModel):
    """Mean of ONE condition's readings over a window — never mixed."""
    condition: WeighCondition
    readings: int
    mean_kg: float


class BodyweightSummary(BaseModel):
    start: Date
    end: Date
    averages: list[ConditionAverage] = Field(default_factory=list)


class BodyweightHistory(BaseModel):
    window_days: int
    start: Date
    end: Date
    readings: list[BodyweightReading]
    last_7_days: BodyweightSummary
    window: BodyweightSummary
