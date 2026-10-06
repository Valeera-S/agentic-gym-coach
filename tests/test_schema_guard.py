"""P75: the code refuses to run against a database at the wrong schema revision."""
import duckdb
import pytest

import coach_tools
from skills import init

PREV = "0004_decision_payload"


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Point skills.init at a fresh scratch DB file (the shared test DB is untouched)."""
    init.close_all()
    path = tmp_path / "scratch.duckdb"
    monkeypatch.setattr(init, "_DUCK_PATH", path)
    monkeypatch.setattr(init, "_schema_ok", set(), raising=False)
    yield path
    init.close_all()


def _stamp(path, rev):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
    if rev is not None:
        con.execute("INSERT INTO alembic_version VALUES (?)", [rev])
    con.close()


def _call(cmd):
    try:
        return coach_tools.DISPATCH[cmd]({})
    except Exception as e:
        return coach_tools.error_payload(e)


def _upgrade(path):
    from alembic import command
    from alembic.config import Config
    import conftest
    cfg = Config(str(conftest._REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{path}")
    command.upgrade(cfg, "head")


@pytest.mark.parametrize("cmd", list(coach_tools.DISPATCH))
def test_behind_db_blocks_every_tool(scratch, cmd):
    _stamp(scratch, PREV)
    out = _call(cmd)
    assert out["error"] == "db"
    d = out["detail"]
    assert PREV in d and init.REQUIRED_SCHEMA_REVISION in d
    assert "alembic upgrade head" in d and "OUTSIDE" in d and "/mcp" in d


def test_behind_db_is_not_written(scratch):
    _stamp(scratch, PREV)
    _call("memory_save")
    _call("bodyweight_log")
    init.close_all()
    con = duckdb.connect(str(scratch))
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    con.close()
    assert tables == {"alembic_version"}


def test_ahead_db_says_update_code(scratch):
    _stamp(scratch, "9999_future")
    out = _call("sessions")
    assert out["error"] == "db"
    assert "9999_future" in out["detail"] and "Update the code" in out["detail"]
    assert "Do NOT downgrade" in out["detail"]


@pytest.mark.parametrize("version_table", [False, True])
def test_empty_db_says_create(scratch, version_table):
    if version_table:
        _stamp(scratch, None)
    out = _call("sessions")
    assert out["error"] == "db"
    assert "alembic upgrade head" in out["detail"] and "create" in out["detail"]


def test_upgrade_after_failure_heals_without_restart(scratch):
    assert _call("sessions")["error"] == "db"  # empty file
    init.close_all()
    _upgrade(scratch)
    assert "error" not in _call("sessions")


def test_passing_check_is_cached(scratch, monkeypatch):
    _upgrade(scratch)
    seen = []
    real = init.get_duckdb

    class Spy:
        def __init__(self, con):
            self.con = con

        def execute(self, sql, *a):
            if "alembic_version" in sql:
                seen.append(sql)
            return self.con.execute(sql, *a)

    monkeypatch.setattr(init, "get_duckdb", lambda: Spy(real()))
    for _ in range(4):
        init.ensure_schema_current()
    assert len(seen) == 2  # table probe + version read, first call only


def test_required_revision_equals_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import conftest
    cfg = Config(str(conftest._REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(conftest._REPO_ROOT / "migrations"))
    assert ScriptDirectory.from_config(cfg).get_current_head() == init.REQUIRED_SCHEMA_REVISION
