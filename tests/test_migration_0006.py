"""Migration 0006 — sessions.label (optional user-chosen workout label)."""

from __future__ import annotations

import duckdb
import pytest
from alembic import command
from alembic.config import Config


def _cfg(db) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{db}")
    return cfg


def _rev(db) -> str:
    con = duckdb.connect(str(db))
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()


_ROW = ("INSERT INTO sessions (date, phase, kind, exercises) "
        "SELECT DATE '2026-10-01', 'accumulation', 'training', []")


def test_upgrade_adds_a_null_label_and_keeps_rows(tmp_path):
    db = tmp_path / "a.duckdb"
    command.upgrade(_cfg(db), "0005_bodyweight_log")
    con = duckdb.connect(str(db))
    con.execute(_ROW)
    before = con.execute("SELECT id, date, kind FROM sessions").fetchall()
    con.close()
    command.upgrade(_cfg(db), "0006_session_label")
    con = duckdb.connect(str(db))
    after = con.execute("SELECT id, date, kind, label FROM sessions").fetchall()
    con.close()
    assert [r[:3] for r in after] == before and all(r[3] is None for r in after)


def test_downgrade_drops_the_column_when_no_label_is_stored(tmp_path):
    db = tmp_path / "b.duckdb"
    command.upgrade(_cfg(db), "0006_session_label")
    command.downgrade(_cfg(db), "0005_bodyweight_log")
    assert _rev(db) == "0005_bodyweight_log"
    con = duckdb.connect(str(db))
    cols = [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'sessions'").fetchall()]
    con.close()
    assert "label" not in cols


def test_downgrade_refuses_while_a_label_is_stored(tmp_path):
    db = tmp_path / "c.duckdb"
    command.upgrade(_cfg(db), "0006_session_label")
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO sessions (date, phase, kind, label, exercises) "
                "SELECT DATE '2026-10-01', 'accumulation', 'training', 'back day', []")
    con.close()
    with pytest.raises(RuntimeError, match="refusing to downgrade 0006"):
        command.downgrade(_cfg(db), "0005_bodyweight_log")
    assert _rev(db) == "0006_session_label"
