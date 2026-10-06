"""pytest rootdir anchor + test DB isolation.

Tests run against a throwaway DuckDB file (NOT the production
data/gym_coach.duckdb), with the schema applied once per session. Set via
env vars read by skills.init BEFORE any test imports it. The production DB
and its backfilled sessions are never touched by the test suite.

Each pytest run gets its own private temp directory (mkdtemp), so runs from
separate checkouts can run at the same time without fighting over one file
(P31). It is removed at session end. Still single-process WITHIN a run (xdist
workers would each get their own DB, but the suite is not tuned for it).
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent
_RUN_DIR = Path(tempfile.mkdtemp(prefix="gym_coach_test_"))
_TEST_DUCK = _RUN_DIR / "gym_coach_test.duckdb"
_TEST_LANCE = _RUN_DIR / "lance"
atexit.register(shutil.rmtree, _RUN_DIR, ignore_errors=True)  # backstop; the fixture cleans first

# Set BEFORE skills.init is imported anywhere.
os.environ["GYM_COACH_DUCKDB"] = str(_TEST_DUCK)
os.environ.setdefault("GYM_COACH_LANCE", str(_TEST_LANCE))


@pytest.fixture(scope="session", autouse=True)
def _init_test_schema():
    """Apply the full schema to the temp DB once per session."""
    from alembic import command
    from alembic.config import Config

    _TEST_DUCK.unlink(missing_ok=True)
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{_TEST_DUCK}")
    command.upgrade(cfg, "head")
    yield
    from skills.init import close_all

    close_all()
    shutil.rmtree(_RUN_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    """The input-date plausibility window (models/dates.py, P38) reads "today"
    through models.dates.today(). Pin it far enough ahead that every fixture
    date in the suite (real-today and the 2030-dated ones) is plausible, so no
    test depends on the wall clock; tests of the window patch it themselves."""
    import datetime
    import models.dates
    monkeypatch.setattr(models.dates, "today", lambda: datetime.date(2035, 1, 1))


_TABLES = ("bodyweight_log", "sessions", "injury_status", "decision_log",
           "phase_snapshots", "user_profiles", "memory_notes")


@pytest.fixture(autouse=True)
def _clean_tables():
    """Wipe the data tables before+after every test so results don't bleed."""
    from skills.init import get_duckdb

    d = get_duckdb()
    for t in _TABLES:
        d.execute(f"DELETE FROM {t}")
    yield
    d = get_duckdb()
    for t in _TABLES:
        d.execute(f"DELETE FROM {t}")