"""DuckDB connection lifecycle — the file is held only for one tool call.

DuckDB allows a single read-write process per file. The MCP server used to
keep its connection between calls, so while it sat idle every other process
(alembic, scripts/ingest_log.py, the CLI) got a bare IOException. Every tool
call now runs inside skills.init.connection_scope().

Cross-process cases use their own scratch DB under tmp_path (migrated to head),
never the suite's shared test DB and never the real data/ directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
from alembic import command
from alembic.config import Config

import coach_tools
import skills.init as init

_REPO = Path(__file__).resolve().parent.parent

# Holds a read-write connection until its stdin closes.
_HOLDER = (
    "import duckdb, sys\n"
    "c = duckdb.connect(sys.argv[1])\n"
    "print('ready', flush=True)\n"
    "sys.stdin.read()\n"
)


@pytest.fixture
def scratch_db(tmp_path):
    db = tmp_path / "lifecycle.duckdb"
    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{db}")
    command.upgrade(cfg, "head")
    return db


def _env_for(db: Path) -> dict:
    return {**os.environ, "GYM_COACH_DUCKDB": str(db),
            "GYM_COACH_LANCE": str(db.parent / "lance")}


def _opens_read_write_elsewhere(db: Path) -> bool:
    """Can a DIFFERENT process open `db` read-write right now?"""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import duckdb, sys; duckdb.connect(sys.argv[1]).execute('SELECT 1').fetchall()",
         str(db)],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode == 0


# --- in-process scope semantics ----------------------------------------------

def test_tool_call_releases_the_connection():
    init.get_duckdb()  # conftest-style ambient connection, opened outside any scope
    coach_tools.DISPATCH["sessions"]({})
    assert init._duck is None
    assert init._scope_depth == 0


def test_tool_call_releases_on_error_too():
    with pytest.raises(ValueError):
        coach_tools.DISPATCH["trend"]({"muscle": "not_a_muscle"})
    assert init._duck is None and init._scope_depth == 0


def test_nested_scopes_share_one_connection_until_the_outermost_exits():
    with init.connection_scope():
        outer = init.get_duckdb()
        with init.connection_scope():
            assert init.get_duckdb() is outer
        assert init._duck is outer  # inner exit must not close it
        outer.execute("SELECT 1").fetchall()
    assert init._duck is None


def test_scope_that_never_touches_the_db_never_opens_it():
    init.close_all()
    with init.connection_scope():
        pass
    assert init._duck is None


def test_connections_reopen_after_release():
    coach_tools.DISPATCH["sessions"]({})
    assert coach_tools.DISPATCH["sessions"]({}) == {"sessions": [], "count": 0}


# --- cross-process -----------------------------------------------------------

def test_other_process_can_write_after_an_in_process_tool_call():
    init.get_duckdb()
    coach_tools.DISPATCH["sessions"]({})
    assert _opens_read_write_elsewhere(init._DUCK_PATH)


def test_idle_mcp_server_does_not_lock_the_db(scratch_db):
    """The P13 scenario end to end: an MCP server that has served a call and
    sits idle; a second process opens the DB read-write and writes; the
    server keeps working and sees the write."""
    server = subprocess.Popen(
        [sys.executable, "mcp_server.py"], cwd=_REPO, env=_env_for(scratch_db),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8",
    )

    def send(msg: dict) -> None:
        server.stdin.write(json.dumps(msg) + "\n")
        server.stdin.flush()

    def recv(msg_id: int) -> dict:
        while True:
            line = server.stdout.readline()
            assert line, "MCP server closed stdout"
            msg = json.loads(line)
            if msg.get("id") == msg_id:
                return msg

    def call_sessions(msg_id: int) -> dict:
        send({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
              "params": {"name": "coach_sessions", "arguments": {"limit": 5}}})
        return recv(msg_id)["result"]

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "lifecycle-test", "version": "0"}}})
        recv(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        first = call_sessions(2)
        # an empty listing is still content (P42): the client can tell "no rows" from a failed call
        assert first["isError"] is False
        assert json.loads(first["content"][0]["text"]) == {"sessions": [], "count": 0}

        # server is idle now: another process must be able to write
        con = duckdb.connect(str(scratch_db))
        con.execute("INSERT INTO sessions (date, phase, exercises) "
                    "VALUES (DATE '2026-10-01', 'maintenance', [])")
        con.close()

        second = call_sessions(3)
        assert second["isError"] is False
        assert "2026-10-01" in second["content"][0]["text"]
    finally:
        server.stdin.close()
        server.terminate()
        server.wait(timeout=30)


def test_genuine_collision_is_a_db_error_with_a_clear_detail(scratch_db):
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(scratch_db)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "ready"
        proc = subprocess.run(
            [sys.executable, "coach_tools.py", "sessions", "{}"],
            cwd=_REPO, env=_env_for(scratch_db),
            capture_output=True, text=True, timeout=60,
        )
    finally:
        holder.stdin.close()
        holder.wait(timeout=30)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["error"] == "db"
    assert payload["exception"] == "IOException"
    assert "in use by another process" in payload["detail"]
    # the holder is gone: the next call simply works (no retry loop needed)
    ok = subprocess.run(
        [sys.executable, "coach_tools.py", "sessions", "{}"],
        cwd=_REPO, env=_env_for(scratch_db), capture_output=True, text=True, timeout=60,
    )
    assert ok.returncode == 0 and json.loads(ok.stdout) == {"sessions": [], "count": 0}


def test_non_lock_io_error_is_not_reported_as_a_lock(tmp_path):
    """Only a lock conflict says "in use by another process": a file that is
    not a DuckDB database will never clear by waiting, so it must not invite a
    retry."""
    bogus = tmp_path / "not_a_db.duckdb"
    bogus.write_bytes(b"this is not a duckdb file" * 100)
    proc = subprocess.run(
        [sys.executable, "coach_tools.py", "sessions", "{}"],
        cwd=_REPO, env=_env_for(bogus), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["error"] == "db"
    assert "in use by another process" not in payload["detail"]
    assert "not a valid DuckDB database" in payload["detail"]


def test_concurrent_tool_calls_from_threads_do_not_interfere():
    """MCP runtimes may run sync tools on worker threads. Scopes serialize, so
    threads never use one DuckDB connection at the same time and the file is
    released once they are all done."""
    from concurrent.futures import ThreadPoolExecutor

    coach_tools.DISPATCH["memory_save"]({"text": "seed"})
    calls = [("sessions", {}), ("memory_search", {}), ("recovery", {}),
             ("trend", {"muscle": "lats"}), ("intake_status", {}),
             ("memory_save", {"text": "t"})] * 20

    def run(call):
        name, args = call
        return coach_tools.DISPATCH[name](args)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, calls))  # re-raises any worker exception
    assert len(results) == len(calls)
    assert init._duck is None and init._scope_depth == 0
    assert coach_tools.DISPATCH["memory_search"]({"limit": 1000})["count"] == 1 + 20
