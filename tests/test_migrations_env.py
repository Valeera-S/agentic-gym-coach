"""migrations/env.py target resolution — GYM_COACH_DUCKDB + parent-dir creation.

The alembic CLI used to ignore GYM_COACH_DUCKDB (it could only ever migrate the
hard-coded data/gym_coach.duckdb) and failed on a fresh clone because
duckdb-engine does not create the missing data/ directory.

Every DB here lives under pytest's tmp_path; the default-target case runs
against a COPY of alembic.ini + migrations/ so the repo's own data/ is never
touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
from alembic import command
from alembic.config import Config

_REPO = Path(__file__).resolve().parent.parent


def _head_tables(db: Path) -> set[str]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    finally:
        con.close()


def test_cli_honours_env_var_and_creates_missing_dirs(tmp_path):
    target = tmp_path / "not" / "yet" / "there" / "eval.duckdb"
    env = {**os.environ, "GYM_COACH_DUCKDB": str(target)}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_REPO, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert target.is_file()
    assert {"sessions", "decision_log", "alembic_version"} <= _head_tables(target)


def test_explicit_url_beats_env_var(tmp_path, monkeypatch):
    """An ambient GYM_COACH_DUCKDB must never redirect a call that named its
    target — this is how conftest.py and programmatic callers migrate."""
    explicit = tmp_path / "explicit" / "a.duckdb"
    ambient = tmp_path / "ambient" / "b.duckdb"
    monkeypatch.setenv("GYM_COACH_DUCKDB", str(ambient))
    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{explicit}")
    command.upgrade(cfg, "head")
    assert explicit.is_file()
    assert not ambient.parent.exists()


def test_default_target_unchanged_without_env_var(tmp_path, monkeypatch):
    """No env var -> the ini default (<ini dir>/data/gym_coach.duckdb), and a
    missing data/ is created: the fresh-clone case."""
    shutil.copy(_REPO / "alembic.ini", tmp_path / "alembic.ini")
    shutil.copytree(_REPO / "migrations", tmp_path / "migrations",
                    ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.delenv("GYM_COACH_DUCKDB", raising=False)
    command.upgrade(Config(str(tmp_path / "alembic.ini")), "head")
    default_db = tmp_path / "data" / "gym_coach.duckdb"
    assert default_db.is_file()
    assert "sessions" in _head_tables(default_db)


def test_env_var_path_with_percent_sign(tmp_path, monkeypatch):
    # set_main_option interpolates; an unescaped '%' would crash the run.
    target = tmp_path / "100%" / "eval.duckdb"
    shutil.copy(_REPO / "alembic.ini", tmp_path / "alembic.ini")
    shutil.copytree(_REPO / "migrations", tmp_path / "migrations",
                    ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("GYM_COACH_DUCKDB", str(target))
    command.upgrade(Config(str(tmp_path / "alembic.ini")), "head")
    assert target.is_file()
    assert not (tmp_path / "data").exists()
