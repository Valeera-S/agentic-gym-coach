"""First run creates the database: a missing or completely empty DuckDB file is
bootstrapped to Alembic head by the first coach tool call. Anything else (behind,
ahead, tables without alembic_version) keeps the P75 guard error untouched —
existing data is never migrated automatically."""
import json
import os
import subprocess
import sys

import duckdb
import pytest

import conftest
from skills import init

ROOT = conftest._REPO_ROOT


def _cli(db, *args):
    env = {**os.environ, "GYM_COACH_DUCKDB": str(db), "GYM_COACH_LANCE": str(db) + ".lance"}
    p = subprocess.run([sys.executable, str(ROOT / "coach_tools.py"), *args],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    return p.returncode, json.loads(p.stdout)


def _revision(db):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()


def _tables(db):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    finally:
        con.close()


def test_missing_file_and_parent_dir_is_bootstrapped_to_head(tmp_path):
    db = tmp_path / "nested" / "dir" / "coach.duckdb"
    code, out = _cli(db, "sessions")
    assert code == 0 and "error" not in out
    assert _revision(db) == init.REQUIRED_SCHEMA_REVISION


def test_second_call_after_bootstrap_works(tmp_path):
    db = tmp_path / "coach.duckdb"
    assert _cli(db, "sessions")[0] == 0
    assert _cli(db, "sessions")[0] == 0  # no connection left open by the bootstrap


def test_existing_but_completely_empty_file_is_bootstrapped(tmp_path):
    db = tmp_path / "coach.duckdb"
    duckdb.connect(str(db)).close()
    assert _tables(db) == set()
    code, out = _cli(db, "sessions")
    assert code == 0 and "error" not in out
    assert _revision(db) == init.REQUIRED_SCHEMA_REVISION


def test_zero_byte_file_is_bootstrapped(tmp_path):
    db = tmp_path / "coach.duckdb"
    db.touch()
    code, out = _cli(db, "sessions")
    assert code == 0 and "error" not in out
    assert _revision(db) == init.REQUIRED_SCHEMA_REVISION


def test_db_behind_head_keeps_guard_error_and_is_not_migrated(tmp_path):
    db = tmp_path / "coach.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
    con.execute("INSERT INTO alembic_version VALUES ('0004_decision_payload')")
    con.close()
    code, out = _cli(db, "sessions")
    assert code == 1 and out["error"] == "db"
    assert "0004_decision_payload" in out["detail"] and "alembic upgrade head" in out["detail"]
    assert _revision(db) == "0004_decision_payload"
    assert _tables(db) == {"alembic_version"}


def test_tables_without_alembic_version_are_not_bootstrapped(tmp_path):
    db = tmp_path / "coach.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE precious (x INTEGER)")
    con.execute("INSERT INTO precious VALUES (42)")
    con.close()
    code, out = _cli(db, "sessions")
    assert code == 1 and out["error"] == "db"
    assert _tables(db) == {"precious"}


def test_empty_alembic_version_table_alone_keeps_guard_error(tmp_path):
    db = tmp_path / "coach.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
    con.close()
    code, out = _cli(db, "sessions")
    assert code == 1 and out["error"] == "db"
    assert _tables(db) == {"alembic_version"}


# --- in-process: failure reporting -------------------------------------------

@pytest.fixture
def scratch(tmp_path, monkeypatch):
    init.close_all()
    path = tmp_path / "x" / "scratch.duckdb"
    monkeypatch.setattr(init, "_DUCK_PATH", path)
    monkeypatch.setattr(init, "_schema_ok", set(), raising=False)
    yield path
    init.close_all()


def test_bootstrap_failure_is_a_db_error_with_recovery_steps(scratch, monkeypatch):
    import coach_tools

    def boom(*_a, **_k):
        raise RuntimeError("migration 0003 exploded")

    monkeypatch.setattr(init, "_run_alembic_upgrade", boom)
    try:
        coach_tools.DISPATCH["sessions"]({})
        raise AssertionError("expected a db error")
    except Exception as e:
        out = coach_tools.error_payload(e)
    assert out["error"] == "db"
    d = out["detail"]
    assert "migration 0003 exploded" in d and "alembic upgrade head" in d
    assert "delete" in d.lower()  # recovery: remove the half-created file and retry


def test_bootstrap_not_attempted_while_this_process_holds_a_connection(scratch, monkeypatch):
    # the file does not exist, but a connection is already open: never run alembic
    # underneath it — fall through to the normal guard error instead
    called = []
    monkeypatch.setattr(init, "_run_alembic_upgrade", lambda *a, **k: called.append(1))
    with init.connection_scope():
        init.get_duckdb()  # creates the file, holds the lock
        with pytest.raises(init.SchemaMismatchError):
            init.ensure_schema_current()
    assert called == []
