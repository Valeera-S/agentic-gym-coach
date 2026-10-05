"""Migration 0004 — decision_log.payload (the amend/delete audit snapshot)."""

from __future__ import annotations

import io

import duckdb
import pytest
from alembic import command
from alembic.config import Config

_REPO_INI = "alembic.ini"


def _cfg(db, buf=None) -> Config:
    cfg = Config(_REPO_INI, output_buffer=buf) if buf else Config(_REPO_INI)
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{db}")
    return cfg


def _rev(db) -> str:
    con = duckdb.connect(str(db))
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()


def test_upgrade_adds_a_null_payload_and_keeps_existing_audit_rows(tmp_path):
    db = tmp_path / "a.duckdb"
    command.upgrade(_cfg(db), "0003_vocab_exercise_fields")
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO decision_log (event_type, trigger_signal, reasoning_chain) "
                "VALUES ('goal_change', 'goal changed', 'why')")
    before = con.execute("SELECT id, event_type, trigger_signal, reasoning_chain, created_at "
                         "FROM decision_log").fetchall()
    con.close()
    command.upgrade(_cfg(db), "0004_decision_payload")
    con = duckdb.connect(str(db))
    after = con.execute("SELECT id, event_type, trigger_signal, reasoning_chain, created_at "
                        "FROM decision_log").fetchall()
    payload = con.execute("SELECT payload FROM decision_log").fetchone()[0]
    con.close()
    assert after == before and payload is None


def test_downgrade_refuses_while_snapshots_are_stored(tmp_path):
    db = tmp_path / "b.duckdb"
    command.upgrade(_cfg(db), "0004_decision_payload")
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO decision_log (event_type, payload) VALUES ('session_delete', '{\"before\": {}}')")
    con.close()
    with pytest.raises(RuntimeError, match="refusing to downgrade 0004"):
        command.downgrade(_cfg(db), "0003_vocab_exercise_fields")
    assert _rev(db) == "0004_decision_payload"


def test_downgrade_drops_the_column_when_no_snapshot_is_stored(tmp_path):
    db = tmp_path / "c.duckdb"
    command.upgrade(_cfg(db), "0004_decision_payload")
    command.downgrade(_cfg(db), "0003_vocab_exercise_fields")
    con = duckdb.connect(str(db))
    cols = {r[0] for r in con.execute("DESCRIBE decision_log").fetchall()}
    con.close()
    assert "payload" not in cols and _rev(db) == "0003_vocab_exercise_fields"


def test_offline_scripts_render_with_a_guard(tmp_path):
    for direction, revs in (("upgrade", "0003_vocab_exercise_fields:0004_decision_payload"),
                            ("downgrade", "0004_decision_payload:0003_vocab_exercise_fields")):
        buf = io.StringIO()
        getattr(command, direction)(_cfg(tmp_path / "unused.duckdb", buf), revs, sql=True)
        script = buf.getvalue()
        assert script.lstrip().startswith("BEGIN")
        if direction == "downgrade":
            assert "refusing to downgrade 0004" in script
    assert not (tmp_path / "unused.duckdb").exists()
