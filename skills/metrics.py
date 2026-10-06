"""metrics — shared training math (one definition per doctrine constant).

Single home of the Epley est-1RM doctrine (Training ch04): estimates come
from sets with 1 <= reps <= EPLEY_MAX_REPS only ("~5RM or heavier"); higher-rep sets
produce no estimate at all, not even as reference detail.

Used by trend_analysis and snapshot. Editing the formula or the cap here
changes every consumer at once — coach_trend and coach_snapshot can never
report different 1RMs for the same logged data.
"""

from __future__ import annotations

import polars as pl

EPLEY_MAX_REPS = 6  # ~5RM-or-heavier doctrine (Training ch04)


def est_1rm(weight_kg: float, reps: float) -> float:
    """Scalar Epley estimate for one qualifying set (reps <= EPLEY_MAX_REPS)."""
    return weight_kg * (1.0 + reps / 30.0)


def epley_expr(weight_col: str, reps_col: str) -> pl.Expr:
    """Polars expression: the Epley formula over named per-set columns."""
    return pl.col(weight_col) * (1.0 + pl.col(reps_col) / 30.0)


def qualifies_for_est_1rm(weight_col: str, reps_col: str) -> pl.Expr:
    """Polars predicate: the set is loaded and heavy enough to estimate from."""
    return (
        pl.col(weight_col).is_not_null()
        & pl.col(reps_col).is_not_null()
        & (pl.col(reps_col) >= 1)  # a 0-rep (or <1) set is not a performance (P55)
        & (pl.col(reps_col) <= EPLEY_MAX_REPS)
    )
