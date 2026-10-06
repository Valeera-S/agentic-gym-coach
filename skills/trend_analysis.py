"""trend_analysis — training trend over a rolling window (Polars).

Volume currency = **effective hard sets** per Helms (Muscle & Strength
Pyramid: Training ch03): count SETS in an intensity zone, never volume-load
(sets×reps×load distorts — 3×25×100 shows 78% more "volume" than 3×10×140
for equal hypertrophy). Rules:
  - primary AND secondary muscle contributions count 1:1 (secondary map:
    models.exercise_catalog.SECONDARY_OVERLAP, derived from the ch03 chart);
    each set credits each muscle AT MOST ONCE, even when the stored primary
    is also one of the identity's derived secondaries
  - stored exercise names are resolved through the catalog first, so a row
    stored under a name that is now an alias (or a pre-split legacy name)
    still credits its identity's muscles
  - form_quality < 3 discounts a set 50% (SPEC §1.3) — effective hard sets only,
    never tonnage (P48)
  - a RECORDED 0-rep set is not a hard set (P55); an unrecorded rep count still is
  - bodyweight/unloaded sets are hard sets (count 1.0 each); tonnage is
    reported in `detail` for reference only, as total external load:
    weight_kg is the reading on the implement, so per_hand / per_side loads
    count twice (both limbs work with that load each). Sets whose load_type is
    unknown (NULL — every pre-0003 row) are counted as read and their number
    is reported as `load_type_unknown_sets`. A Smith machine's bar weight is
    unknowable and never included.

est_1rm_kg: Epley over sets with reps <= 6 only ("estimate 1RM only from
~5RM-or-heavier performances", ch04). Formula and cap live in skills/metrics.py
— the single definition shared with snapshot. No uncapped estimate is reported
anywhere: Epley at 12+ reps is badly off, and a number shown beside the
correctly-null est_1rm_kg would read as usable.

trend_direction: est-1RM trend across window halves when qualifying heavy
sets exist in both halves (±2%). Otherwise — the usual hypertrophy case, most
sets in the 6-12 range (ch03) — direction is judged on PERFORMANCE per
exercise identity, the double-progression semantics of ch04:
  up   = more load at >= the same reps, or more reps at the same load
  down = the reverse;  anything else = flat
Each identity's top set per half (heaviest load, most reps at that load) is
compared; bodyweight sets count as load 0, so reps decide. A weight that was
simply not recorded is NOT zero: such sets are left out of the comparison
(never interpolate a load). A load step (heavier, reps back down — the second
half of double progression) counts as `up` when the heavier top set is still a
working set (EVERY set at the heavier load >= 6 reps) and its Epley estimate
does not drop; symmetric for `down` (HEURISTIC, human decisions 2026-10-05:
Epley is used only as a relative comparison inside one identity and is never
reported as a 1RM). Bodyweight exercises get no load step — their true load
includes a body mass the system does not have per session — so they compare
by dominance only (more added load at >= the reps, or more reps). Only exercises for
which the muscle is a chart PRIMARY (for an unknown name: its stored muscle)
decide its direction (overlap credit counts
toward volume, never toward progression). Per muscle, whichever of up / down
carries more effective sets wins; equal (or all flat) -> plateau; nothing
comparable across both halves -> unknown. A set-count change alone is a volume
change, never progression. `stalled` = plateau or down with >=4 sessions — a
flag, not a verdict: the coach must run the plateau flowchart (free-wins →
recovery checklist) before acting on it.

Habit sessions (kind='habit', e.g. 60 bodyweight squats daily) count toward
VOLUME only: a deliberately constant routine guarantees a false plateau, so
direction (both paths), `sessions_in_window` and the >=4-session `stalled`
rule use training sessions only (a NULL kind counts as training). A muscle
trained only by habits reads `unknown`, not stalled, with its volume intact.

Contract: <100ms on a 10K-row synthetic set (AGENTS.md checklist; -m slow guards).
Deterministic given the logged data.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl

from models import LoadType, MuscleGroup, TrendDirection, TrendReport
from models.exercise_catalog import (
    credited_muscles,
    default_load_type,
    exercises_crediting,
    lookup_key,
    progression_muscles,
    resolve_name,
)

from .init import get_duckdb
from .metrics import epley_expr, est_1rm, qualifies_for_est_1rm


def window_start(end: date, window_days: int) -> date:
    """First calendar day of a `window_days`-day window ending at `end`.

    A window of N days is exactly N calendar days INCLUDING `end` (end-N+1 ..
    end), so the inclusive SQL range never reaches a day too far back (P29: the
    old `end - N` start made every window N+1 days). Single definition — trend,
    snapshot and any caller of hard_sets_by_muscle derive their start here
    (recovery's 7-day counts, end-7 .. end-1 excluding the query day, are
    already exactly 7 days).
    """
    return end - timedelta(days=window_days - 1)


# P26: the per-set arrays of a stored exercise can be a NULL ARRAY (not an
# array of nulls) — a row written outside log_session. Read it as "every set
# unrecorded", the same way ExerciseModel pads an empty array: a list of None
# as long as the exercise's sets. Without this, a NULL array explodes to ONE
# null row while its sibling arrays explode to `sets` rows and Polars rejects
# the mismatch. A reader selects the raw `reps_raw` / `rpe_raw` / `weight_raw`
# columns plus `sets` in an inner query, adds SET_COUNT_SQL in a middle one and
# splices PADDED_SET_ARRAYS_SQL into the outer select; every reader that
# explodes the three arrays together uses this pair.
#
# P47: a legacy row's arrays may also disagree with each other or with `sets`.
# `sets` is the authority: every array is aligned to it — padded with NULL
# (unrecorded) when shorter, truncated when longer — so they always explode
# to the same row count.
SET_COUNT_SQL = "greatest(coalesce(sets, 0), 0) AS _n"
PADDED_SET_ARRAYS_SQL = """
        list_resize(coalesce(reps_raw, []::FLOAT[]), _n) AS reps,
        list_resize(coalesce(rpe_raw, []::FLOAT[]), _n) AS rpe,
        list_resize(coalesce(weight_raw, []::DOUBLE[]), _n) AS weight_kg"""


def _fetch_entries(start: date, end: date) -> pl.DataFrame:
    """Entry-level rows: one row per exercise within a session."""
    sql = f"""
        SELECT date, kind, name, mg, sets, form_quality, load_type, {PADDED_SET_ARRAYS_SQL}
        FROM (
          SELECT *, {SET_COUNT_SQL}
          FROM (
            SELECT s.date,
                   s.kind AS kind,
                   UNNEST(s.exercises).name AS name,
                   CAST(UNNEST(s.exercises).muscle_group AS VARCHAR) AS mg,
                   UNNEST(s.exercises).sets AS sets,
                   UNNEST(s.exercises).reps AS reps_raw,
                   UNNEST(s.exercises).rpe AS rpe_raw,
                   UNNEST(s.exercises).weight_kg AS weight_raw,
                   UNNEST(s.exercises).form_quality AS form_quality,
                   UNNEST(s.exercises).load_type AS load_type
            FROM sessions s
            WHERE s.date BETWEEN ? AND ?
          )
        )
    """
    df = get_duckdb().execute(sql, [start, end]).pl()
    # P55: a recorded 0-rep set is not a hard set; an unrecorded count still is
    return df.with_columns((
        pl.col("sets").fill_null(0)
        - pl.col("reps").list.eval(pl.element() <= 0).list.sum().fill_null(0)
    ).alias("hard_sets"))


# Load types whose reading is ONE limb's load while both limbs work.
_BOTH_LIMBS = ["per_hand", "per_side"]


def _with_form_mult(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.when(pl.col("form_quality") < 3).then(0.5).otherwise(1.0).alias("form_mult"),
    )


def with_identity(df: pl.DataFrame) -> pl.DataFrame:
    """Add `identity`: each stored name resolved to its catalog identity
    (unknown names stay as stored)."""
    mapping = {n: resolve_name(n) or n for n in df["name"].unique().to_list() if n is not None}
    return df.with_columns(pl.col("name").replace(mapping).alias("identity"))


def hard_sets_by_muscle(start: date, end: date) -> dict[str, float]:
    """Effective hard sets per muscle group in [start, end], overlap-inclusive.

    Shared by snapshot — single definition of "weekly volume" across skills.
    """
    df = _fetch_entries(start, end)
    if df.height == 0:
        return {}
    df = _with_form_mult(df)
    out: dict[str, float] = {}
    for name, mg, sets, mult in df.select("name", "mg", "hard_sets", "form_mult").iter_rows():
        if name is None:
            continue  # legacy nameless entry: credits no muscle (P47)
        for m in credited_muscles(name, mg):
            out[m] = out.get(m, 0.0) + (sets or 0) * mult
    return {k: float(v) for k, v in out.items()}


def _top_set(sets: list[tuple[float, float]]) -> tuple[float, float]:
    """Heaviest load, and the most reps achieved at that load."""
    top_load = max(load for load, _ in sets)
    return top_load, max(reps for load, reps in sets if load == top_load)


def _beats(sets: list[tuple[float, float]], ref: tuple[float, float]) -> bool:
    """Some set does more load at >= the reps, or more reps at >= the load."""
    return any(load >= ref[0] and reps >= ref[1] and (load > ref[0] or reps > ref[1])
               for load, reps in sets)


# HEURISTIC (human decision 2026-10-05) — the load step of double progression:
# heavier, with reps back down, is progress while the heavier set is still a
# working set and its Epley estimate (skills/metrics.py) does not drop. Epley
# is only a relative yardstick between two sets of ONE exercise here — never
# reported as a 1RM, so its error at higher reps does not reach the user.
_LOAD_STEP_MIN_REPS = 6  # the lower edge of a hypertrophy working set (ch03); human decision


def _load_step(heavier_half: list[tuple[float, float]], heavier: tuple[float, float],
               lighter: tuple[float, float]) -> bool:
    """The heavier half's top set is a load step over the lighter half's: more
    load, EVERY set at that load still >= 6 reps, Epley not lower (equal
    counts — compared with a float tolerance so 20 lb x 15 vs 25 lb x 6, both
    30, is not lost to rounding)."""
    if heavier[0] <= lighter[0]:
        return False
    if min(r for load, r in heavier_half if load == heavier[0]) < _LOAD_STEP_MIN_REPS:
        return False
    h, l = est_1rm(*heavier), est_1rm(*lighter)
    return h >= l or math.isclose(h, l, rel_tol=1e-9, abs_tol=1e-9)


def _is_bodyweight(name: str, stored_load_type: str | None) -> bool:
    """Whether a null weight on this row means 'no external load' (counts as 0)
    rather than 'not recorded' (unknown). Legacy rows (no stored load_type)
    fall back to the identity's catalog default."""
    if stored_load_type is not None:
        return stored_load_type == LoadType.bodyweight.value
    return default_load_type(name) is LoadType.bodyweight


def _performance_direction(df: pl.DataFrame, muscle: str,
                           first_dates: set) -> tuple[TrendDirection, dict[str, str]]:
    """Double-progression direction for `muscle` from per-identity top sets."""
    halves: dict[str, tuple[list, list]] = {}
    weight: dict[str, float] = {}
    shown: dict[str, str] = {}
    bodyweight_keys: set[str] = set()
    for d, name, ident, mg, sets, reps, loads, mult, load_type in df.select(
            "date", "name", "identity", "mg", "hard_sets", "reps", "weight_kg", "form_mult",
            "load_type").iter_rows():
        if muscle not in progression_muscles(name, mg):
            continue  # overlap credit: volume only, never progression
        bodyweight = _is_bodyweight(name, load_type)
        reps = reps or []
        loads = loads or [None] * len(reps)
        pairs = [(0.0 if w is None else w, r) for r, w in zip(reps, loads)
                 if r is not None and (w is not None or bodyweight)]
        # unknown names group case-insensitively ("meadows row" == "Meadows Row")
        key = ident if resolve_name(name) else lookup_key(name)
        shown.setdefault(key, ident)
        # The load-step exclusion is per IDENTITY: a bodyweight exercise stays
        # one even when the caller declares an added-load reading ("total" for
        # a belt-weighted pull-up) — its true load still includes body mass.
        if bodyweight or default_load_type(name) is LoadType.bodyweight:
            bodyweight_keys.add(key)
        first, second = halves.setdefault(key, ([], []))
        (first if d in first_dates else second).extend(pairs)
        weight[key] = weight.get(key, 0.0) + (sets or 0) * mult

    verdicts: dict[str, str] = {}
    up_sets = down_sets = 0.0
    for key, (first, second) in sorted(halves.items()):
        if not first or not second:
            continue  # not done (with a recorded load) in both halves: nothing to compare
        ident = shown[key]
        t1, t2 = _top_set(first), _top_set(second)
        up, down = _beats(second, t1), _beats(first, t2)
        if key not in bodyweight_keys:  # bodyweight: dominance only (human decision)
            up = up or _load_step(second, t2, t1)
            down = down or _load_step(first, t1, t2)
        verdict = "up" if up and not down else "down" if down and not up else "flat"
        verdicts[ident] = verdict
        if verdict == "up":
            up_sets += weight[key]
        elif verdict == "down":
            down_sets += weight[key]

    if not verdicts:
        return TrendDirection.unknown, verdicts
    if up_sets > down_sets:
        return TrendDirection.up, verdicts
    if down_sets > up_sets:
        return TrendDirection.down, verdicts
    return TrendDirection.plateau, verdicts


def get_specialization_trend(
    muscle: MuscleGroup, window_days: int = 28, end_date: date | None = None
) -> TrendReport:
    if muscle is MuscleGroup.unclassified:
        raise ValueError("'unclassified' is not a muscle: its sets credit no muscle's "
                         "volume or trend; map the exercise instead")
    # end_date lets the coach analyze/backtest historical slices; default today.
    anchor = end_date or date.today()
    start = window_start(anchor, window_days)
    # a legacy nameless entry credits no muscle (P47), whatever its stored muscle
    df = _fetch_entries(start, anchor).filter(pl.col("name").is_not_null())

    crediting = list(exercises_crediting(muscle))
    df = with_identity(df).filter(
        (pl.col("mg") == muscle.value) | pl.col("identity").is_in(crediting)
    )

    if df.height == 0:
        return TrendReport(
            muscle=muscle, window_days=window_days, effective_volume=0.0,
            avg_rpe=None, est_1rm_kg=None, stalled=False,
            trend_direction=TrendDirection.unknown, sessions_in_window=0,
            detail={"load_type_unknown_sets": 0, "unloaded_sets": 0, "overlap_sets": 0.0,
                    "direction_basis": None, "identity_directions": {}},
        )

    df = _with_form_mult(df)
    # credited other than through the row's stored primary (a row whose
    # stored primary IS the muscle is a primary credit, never also an overlap)
    df = df.with_columns(
        ((pl.col("mg") != muscle.value) & pl.col("identity").is_in(crediting))
        .fill_null(False).alias("is_overlap"),
    )

    # --- hard sets (primary metric) ---------------------------------------
    hard_sets = df.select((pl.col("hard_sets") * pl.col("form_mult")).sum()).item() or 0.0
    overlap_sets = (
        df.filter(pl.col("is_overlap"))
        .select((pl.col("hard_sets") * pl.col("form_mult")).sum()).item() or 0.0
    )

    # --- per-set metrics (reference only) ----------------------------------
    # empty_as_null=True is Polars' CURRENT default (an empty array becomes one
    # null row); stated so a Polars 2.0 default flip cannot silently change
    # the per-set counts (P28)
    per_set = df.explode(["reps", "rpe", "weight_kg"], empty_as_null=True)
    load_mult = (
        pl.when(pl.col("load_type").cast(pl.Utf8).is_in(_BOTH_LIMBS)).then(2.0).otherwise(1.0)
    )
    tonnage = per_set.select(
        (pl.col("reps") * pl.col("weight_kg") * load_mult).sum()
    ).item() or 0.0
    loaded = pl.col("weight_kg").is_not_null() & pl.col("reps").is_not_null()
    load_type_unknown = int(per_set.filter(loaded & pl.col("load_type").is_null()).height)
    avg_rpe = per_set.select(pl.col("rpe").mean()).item()

    qualifies = qualifies_for_est_1rm("weight_kg", "reps")
    est_1rm = per_set.filter(qualifies).select(epley_expr("weight_kg", "reps").max()).item()
    unloaded = int(per_set.filter(pl.col("weight_kg").is_null()).height)

    # --- trend across first vs second half of the window (by date) ---------
    # habit sessions are volume-only: direction and session counting use
    # training rows alone (NULL kind = legacy training)
    is_training = pl.col("kind").fill_null("training") != "habit"
    df = df.filter(is_training)
    per_set = per_set.filter(is_training)
    sessions = df["date"].unique().sort().len()
    trend_direction = TrendDirection.unknown
    direction_basis: str | None = None
    identity_directions: dict[str, str] = {}
    if sessions >= 4:
        dates = df["date"].unique().sort()
        mid = dates.len() // 2
        first_dates = set(dates.head(mid).to_list())
        in_first = pl.col("date").is_in(list(first_dates))

        tf_est = per_set.filter(in_first & qualifies).select(
            epley_expr("weight_kg", "reps").max()
        ).item()
        ts_est = per_set.filter(~in_first & qualifies).select(
            epley_expr("weight_kg", "reps").max()
        ).item()
        if tf_est is not None and ts_est is not None:
            # strength progress: est-1RM trend, ±2% band
            if ts_est > tf_est * 1.02:
                trend_direction = TrendDirection.up
            elif ts_est < tf_est * 0.98:
                trend_direction = TrendDirection.down
            else:
                trend_direction = TrendDirection.plateau
            direction_basis = "est_1rm"
        else:
            trend_direction, identity_directions = _performance_direction(
                df, muscle.value, first_dates)
            direction_basis = "performance"

    stalled = trend_direction in (TrendDirection.plateau, TrendDirection.down)

    return TrendReport(
        muscle=muscle, window_days=window_days,
        effective_volume=float(hard_sets),
        avg_rpe=round(float(avg_rpe), 2) if avg_rpe is not None else None,
        est_1rm_kg=round(float(est_1rm), 1) if est_1rm is not None else None,
        stalled=stalled, trend_direction=trend_direction,
        sessions_in_window=int(sessions),
        detail={
            "tonnage_kg": round(float(tonnage), 1),
            "load_type_unknown_sets": load_type_unknown,
            "unloaded_sets": unloaded,
            "overlap_sets": float(overlap_sets),
            "direction_basis": direction_basis,  # est_1rm | performance | None (<4 sessions)
            "identity_directions": identity_directions,  # performance basis only
        },
    )
