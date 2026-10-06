"""coach_session_detail — read a logged session back exactly as logged.

Before this tool nothing could return sessions.exercises: the Coach could not
answer "what did I do last time" or "how much did I bench".
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import mcp_server
from coach_tools import DISPATCH, error_payload
from models import ExerciseModel, SessionInput
from models.session import LB_TO_KG
from skills.init import get_duckdb
from skills.session_logger import log_session

D = date(2026, 10, 4)
_REPO = Path(__file__).resolve().parents[2]


def _detail(**args) -> list[dict]:
    return DISPATCH["session_detail"](args)["sessions"]


def _full_session():
    return log_session(SessionInput(
        date=D, phase="accumulation", pre_recovery_score=82, post_feedback="solid day",
        exercises=[
            ExerciseModel(name="  db bench press ", sets=3, reps=[10, 9, 8], rpe=[8, 8.5, 9],
                          weight=[25, 25, 22.5], unit="lb", tempo="3-1-X-1", notes="paused"),
            ExerciseModel(name="Lat Pulldown", sets=2, reps=[10, 10], weight_kg=[27.2, 27.2],
                          load_type="machine_stack", form_quality=2, pain_flag=True),
            ExerciseModel(name="Meadows Row", sets=2, reps=[12, 12]),
            ExerciseModel(name="Zercher Carry", sets=1, reps=[30]),
            ExerciseModel(name="Strange Thing", muscle_group="mid_back", sets=1, reps=[10]),
            ExerciseModel(name="Dip", muscle_group="chest", sets=2, reps=[8, 8]),
        ]))


def test_every_logged_field_reads_back_exactly():
    conf = _full_session()
    (s,) = _detail(session_id=str(conf.session_id))
    assert s["id"] == str(conf.session_id)
    assert (s["date"], s["phase"], s["kind"]) == ("2026-10-04", "accumulation", "training")
    assert (s["pre_recovery_score"], s["post_feedback"]) == (82, "solid day")
    bench, pulldown, meadows, zercher, strange, dip = s["exercises"]

    assert bench["name"] == "Dumbbell Bench Press"
    assert bench["raw_name"] == "  db bench press "
    assert (bench["muscle_group"], bench["muscle_source"]) == ("chest", "catalog")
    assert bench["reps"] == [10, 9, 8] and bench["rpe"] == [8, 8.5, 9]
    assert bench["weight_as_entered"] == [25, 25, 22.5] and bench["unit_as_entered"] == "lb"
    assert bench["entered_weight"] == [25, 25, 22.5] and bench["entered_unit"] == "lb"
    assert bench["weight_kg"] == [25 * LB_TO_KG, 25 * LB_TO_KG, 22.5 * LB_TO_KG]
    assert (bench["load_type"], bench["load_type_unknown"]) == ("per_hand", False)
    assert (bench["tempo"], bench["notes"], bench["form_quality"]) == ("3-1-X-1", "paused", 5)
    assert bench["needs_review"] is False and bench["review_detail"] is None

    # a weight_kg caller's own numbers ARE the kg values
    assert pulldown["weight_as_entered"] == [27.2, 27.2] and pulldown["unit_as_entered"] == "kg"
    assert pulldown["entered_weight"] is None and pulldown["entered_unit"] is None
    assert (pulldown["form_quality"], pulldown["pain_flag"]) == (2, True)

    assert (meadows["muscle_source"], meadows["needs_review"]) == ("keyword", True)
    assert "guessed by keyword" in meadows["review_detail"]
    assert (zercher["muscle_group"], zercher["needs_review"]) == ("unclassified", True)
    assert (strange["muscle_source"], strange["needs_review"]) == ("caller", True)
    assert "set by caller" in strange["review_detail"]
    assert (dip["muscle_source"], dip["needs_review"]) == ("caller", False)  # known name
    assert s["needs_review_count"] == 3


def test_unknown_load_type_is_flagged():
    log_session(SessionInput(date=D, exercises=[ExerciseModel(name="Row", sets=1, reps=[10],
                                                              weight_kg=[40])]))
    (ex,) = _detail(date="2026-10-04")[0]["exercises"]
    assert ex["load_type"] is None and ex["load_type_unknown"] is True


def test_lookup_by_date_returns_every_session_in_logging_order():
    a = log_session(SessionInput(date=D, exercises=[ExerciseModel(name="Squat", sets=1)]))
    b = log_session(SessionInput(date=D, kind="habit",
                                 exercises=[ExerciseModel(name="Bodyweight Squat", sets=3)]))
    log_session(SessionInput(date=date(2026, 10, 3), exercises=[ExerciseModel(name="Dip", sets=1)]))
    got = _detail(date="2026-10-04")
    assert [s["id"] for s in got] == [str(a.session_id), str(b.session_id)]
    assert [s["kind"] for s in got] == ["training", "habit"]


@pytest.mark.parametrize("args", [
    {"session_id": "00000000-0000-0000-0000-000000000000"},  # unknown id
    {"date": "2026-01-01"},                                   # no session that day
    {},                                                       # neither
    {"session_id": "00000000-0000-0000-0000-000000000000", "date": "2026-10-04"},  # both
    {"session_id": "not-a-uuid"},
    {"session_id": 12345},
    {"date": "04/10/2026"},
])
def test_bad_lookups_are_invalid_input(args):
    log_session(SessionInput(date=D, exercises=[ExerciseModel(name="Squat", sets=1)]))
    with pytest.raises(Exception) as exc:
        DISPATCH["session_detail"](args)
    assert error_payload(exc.value)["error"] == "invalid_input"


def test_legacy_rows_read_back_with_unknown_provenance():
    """A row shaped like pre-0003 storage (no raw_name / source / load_type)."""
    get_duckdb().execute(
        "INSERT INTO sessions (date, phase, exercises) VALUES (DATE '2026-09-19', 'maintenance', ?)",
        [[{"name": "Fly", "muscle_group": "chest", "sets": 3, "reps": [12, 12, 12],
           "weight_kg": [6.8, 6.8, 6.8]},
          {"name": "Mystery Press", "muscle_group": "core", "sets": 2, "reps": [None, None]}]])
    (s,) = _detail(date="2026-09-19")
    fly, mystery = s["exercises"]
    assert fly["raw_name"] is None and fly["muscle_source"] is None
    assert fly["needs_review"] is False            # a catalog identity
    assert fly["weight_as_entered"] == [6.8, 6.8, 6.8] and fly["unit_as_entered"] == "kg"
    assert mystery["needs_review"] is True and mystery["reps"] == [None, None]
    assert "not in the catalog" in mystery["review_detail"]


def test_sessions_listing_gains_a_needs_review_count_and_stays_lean():
    _full_session()
    (row,) = DISPATCH["sessions"]({})
    assert row["needs_review"] == 3
    assert "exercises" not in row  # lean by design (Tier-1 budget)


def test_cli_and_mcp_surfaces_agree(tmp_path):
    conf = _full_session()
    via_mcp = mcp_server.coach_session_detail(session_id=str(conf.session_id))
    assert via_mcp["sessions"][0]["needs_review_count"] == 3
    assert mcp_server.coach_session_detail(date="2026-01-01")["error"] == "invalid_input"

    # CLI on its own scratch DB
    from alembic import command
    from alembic.config import Config
    db = tmp_path / "detail.duckdb"
    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{db}")
    command.upgrade(cfg, "head")
    env = {"GYM_COACH_DUCKDB": str(db), "GYM_COACH_LANCE": str(tmp_path / "lance")}
    import os
    env = {**os.environ, **env}
    run = lambda *a: subprocess.run([sys.executable, "coach_tools.py", *a], cwd=_REPO, env=env,
                                    capture_output=True, text=True, timeout=60)
    logged = json.loads(run("log_session", json.dumps({"date": "2026-10-04", "exercises": [
        {"name": "Cable Fly", "sets": 2, "reps": [12, 12], "weight": [15, 15], "unit": "lb"}]})).stdout)
    out = run("session_detail", json.dumps({"session_id": logged["session_id"]}))
    assert out.returncode == 0
    (ex,) = json.loads(out.stdout)["sessions"][0]["exercises"]
    assert (ex["weight_as_entered"], ex["unit_as_entered"], ex["load_type"]) == ([15, 15], "lb", "per_side")
    bad = run("session_detail", json.dumps({"date": "2026-01-01"}))
    assert bad.returncode == 1 and json.loads(bad.stdout)["error"] == "invalid_input"


def test_float32_rpe_and_reps_read_back_as_logged():
    """reps / rpe are stored as float32; read-back returns what was logged."""
    log_session(SessionInput(date=D, exercises=[ExerciseModel(
        name="Squat", sets=3, reps=[5, 7.5, 3.3], rpe=[7.3, 8.7, 9.1], weight_kg=[102.3] * 3)]))
    (ex,) = _detail(date="2026-10-04")[0]["exercises"]
    assert ex["rpe"] == [7.3, 8.7, 9.1]
    assert ex["reps"] == [5, 7.5, 3.3]
    assert ex["weight_kg"] == [102.3] * 3


def test_unknown_stored_vocabulary_never_breaks_a_read():
    get_duckdb().execute(
        "INSERT INTO sessions (date, phase, exercises) VALUES (DATE '2026-09-20', 'maintenance', ?)",
        [[{"name": "Odd Thing", "muscle_group": "traps", "muscle_source": "future_source",
           "sets": 1}]])
    (ex,) = _detail(date="2026-09-20")[0]["exercises"]
    assert ex["needs_review"] is True and "traps" in ex["review_detail"]


@pytest.mark.slow
def test_sessions_listing_stays_fast_on_a_large_limit():
    import time
    # realistic sessions: six exercises of mixed provenance, per-set arrays
    ex = ("{{'name': '{n}', 'muscle_group': 'lats', 'muscle_source': {s}, 'sets': 4, "
          "'reps': [10.0, 10.0, 9.0, 8.0], 'rpe': [8.0, 8.5, 9.0, 9.5], "
          "'weight_kg': [40.0, 40.0, 40.0, 40.0], 'raw_name': '{n}', 'load_type': 'total'}}")
    exercises = ", ".join(ex.format(n=n, s=s) for n, s in (
        ("Lat Pulldown", "'catalog'"), ("Meadows Row", "'keyword'"), ("Cable Row", "'catalog'"),
        ("Strange Pull", "'caller'"), ("Fly", "NULL"), ("Zercher Carry", "'unclassified'")))
    # plus two unmapped names per session from a pool of 2,500 each: thousands
    # of distinct flagged (name, source) pairs on one page
    odd = ("{'name': 'Odd Pull ' || CAST(i % 2500 AS VARCHAR), 'muscle_group': 'lats', "
           "'muscle_source': 'keyword', 'sets': 3}")
    thing = ("{'name': 'Thing ' || CAST((i * 7) % 2500 AS VARCHAR), 'muscle_group': "
             "'unclassified', 'muscle_source': 'unclassified', 'sets': 3}")
    get_duckdb().execute(
        "INSERT INTO sessions (date, phase, exercises) "
        "SELECT DATE '2026-01-01' + CAST(i % 365 AS INTEGER), 'maintenance', "
        f"[{exercises}, {odd}, {thing}] FROM range(10000) t(i)")
    DISPATCH["sessions"]({"limit": 10000})  # warm up the same path
    t0 = time.perf_counter()
    out = DISPATCH["sessions"]({"limit": 10000})
    dt_ms = (time.perf_counter() - t0) * 1000
    assert len(out) == 10000 and out[0]["needs_review"] == 5
    assert dt_ms < 100, f"listing took {dt_ms:.1f}ms"


def test_listing_memo_holds_a_page_with_thousands_of_distinct_pairs():
    """P20: the (name, source) memo was smaller than a large page's distinct
    pairs, so a cyclic scan evicted every entry before it was reused and a warm
    listing re-resolved every name. A second listing must not miss at all."""
    from skills.sessions import _needs_review, list_sessions
    get_duckdb().execute(
        "INSERT INTO sessions (date, phase, exercises) SELECT DATE '2026-01-01', 'maintenance', "
        "[{'name': 'Odd Pull ' || CAST(i AS VARCHAR), 'muscle_source': 'keyword', 'sets': 1}] "
        "FROM range(5000) t(i)")
    list_sessions(5000)
    misses = _needs_review.cache_info().misses
    out = list_sessions(5000)
    assert len(out) == 5000 and all(s["needs_review"] == 1 for s in out)
    assert _needs_review.cache_info().misses == misses


def test_listing_counts_match_an_independent_recount():
    """Adversarial page: ties on date, NULL / empty lists, odd sources."""
    from skills.sessions import _needs_review, list_sessions
    d = get_duckdb()
    for i in range(30):
        d.execute(
            "INSERT INTO sessions (date, phase, exercises) VALUES (?, 'maintenance', ?)",
            [date(2026, 10, 1 + i % 3), None if i % 7 == 0 else [] if i % 5 == 0 else [
                {"name": "Meadows Row", "muscle_source": "keyword", "sets": 1},
                {"name": "Lat Pulldown", "muscle_source": "catalog", "sets": 1},
                {"name": f"Odd {i % 4}", "muscle_source": None, "sets": 1},
                {"name": "Fly", "muscle_source": None, "sets": 1},
                {"name": "Thing", "muscle_source": "future_source", "sets": 1},
                None]])
    rows = d.execute("SELECT id, exercises FROM sessions ORDER BY date DESC, created_at DESC, id").fetchall()
    # every stored entry counts; a NULL / nameless one always needs review (P22)
    expected = [sum(1 for e in (exs or [])
                    if e is None or _needs_review(e["name"], e["muscle_source"]))
                for _, exs in rows]
    for limit in (0, 1, 7, 30, 31, 100):
        got = list_sessions(limit)
        assert [g["id"] for g in got] == [str(r[0]) for r in rows][:limit]
        assert [g["needs_review"] for g in got] == expected[:limit]


@pytest.mark.parametrize("cmd, arg", [("sessions", "{}"),
                                      ("sessions", '{"limit": 5000}'),
                                      ("session_detail", '{"date": "2026-01-01"}')])
def test_cli_listing_and_detail_never_import_polars(tmp_path, cmd, arg):
    """The CLI's 0.5 s per-call budget: Polars + pyarrow alone cost ~0.3-0.5 s
    to import, so the default listing and the detail tool must not load them."""
    import os
    from alembic import command
    from alembic.config import Config
    db = tmp_path / "budget.duckdb"
    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{db}")
    command.upgrade(cfg, "head")
    import duckdb
    con = duckdb.connect(str(db))  # > 1000 sessions: a large page must stay light too
    con.execute("INSERT INTO sessions (date, phase, exercises) SELECT DATE '2026-01-01', "
                "'maintenance', [{'name': 'Meadows Row', 'muscle_source': 'keyword', 'sets': 1}] "
                "FROM range(1500)")
    con.close()
    probe = ("import runpy, sys; sys.argv = ['coach_tools.py', sys.argv[1], sys.argv[2]]\n"
             "try:\n    runpy.run_path('coach_tools.py', run_name='__main__')\n"
             "except SystemExit:\n    pass\n"
             "print('MODULES', 'polars' in sys.modules, 'pyarrow' in sys.modules)")
    env = {**os.environ, "GYM_COACH_DUCKDB": str(db), "GYM_COACH_LANCE": str(tmp_path / "l")}
    out = subprocess.run([sys.executable, "-c", probe, cmd, arg], cwd=_REPO, env=env,
                         capture_output=True, text=True, timeout=60)
    assert "MODULES False False" in out.stdout, out.stdout + out.stderr
