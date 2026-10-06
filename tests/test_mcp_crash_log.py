"""P73: a dying MCP server must leave evidence. Claude Code's MCP log captured
no stderr when the live server process vanished mid-call, so mcp_server.py
installs `crash_log.install()` at startup: faulthandler + a timestamped
uncaught-exception hook, both writing next to the DB (never to stdout, which
is the MCP protocol channel)."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(tmp_path, body: str):
    db = tmp_path / "gym.duckdb"
    code = ("import sys; sys.path.insert(0, %r)\n"
            "import crash_log; crash_log.install()\n%s\n" % (str(ROOT), body))
    env = dict(os.environ, GYM_COACH_DUCKDB=str(db))
    env.pop("PYTHONFAULTHANDLER", None)
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    return proc, tmp_path / "mcp_server_crash.log"


def test_native_crash_leaves_a_traceback(tmp_path):
    proc, log = _run(tmp_path, "import faulthandler\ndef boom():\n    faulthandler._sigsegv()\nboom()")
    assert proc.returncode != 0
    text = log.read_text(encoding="utf-8")
    assert "Fatal Python error" in text and "boom" in text


def test_abort_leaves_a_traceback(tmp_path):
    proc, log = _run(tmp_path, "import os\ndef die():\n    os.abort()\ndie()")
    assert proc.returncode != 0
    text = log.read_text(encoding="utf-8")
    assert "Fatal Python error" in text and "die" in text


def test_uncaught_exception_is_logged_with_timestamp(tmp_path):
    proc, log = _run(tmp_path, "raise RuntimeError('kaboom 73')")
    assert proc.returncode != 0
    text = log.read_text(encoding="utf-8")
    assert "RuntimeError: kaboom 73" in text and "uncaught exception" in text
    assert text.count("20") >= 1  # ISO timestamp prefix
    assert "kaboom" in proc.stderr  # the default hook still reports on stderr


def test_nothing_is_written_to_stdout(tmp_path):
    proc, _ = _run(tmp_path, "print('', end='')")
    assert proc.stdout == ""


def test_mcp_server_installs_it_at_import():
    src = (ROOT / "mcp_server.py").read_text(encoding="utf-8")
    assert "crash_log.install()" in src


def test_real_server_start_is_logged(tmp_path):
    """Spawn the real stdio server: it logs its start next to the DB and closes on EOF."""
    db = tmp_path / "gym.duckdb"
    env = dict(os.environ, GYM_COACH_DUCKDB=str(db))
    proc = subprocess.run([sys.executable, str(ROOT / "mcp_server.py")], env=env, input="",
                          capture_output=True, text=True, timeout=60)
    assert "server start" in (tmp_path / "mcp_server_crash.log").read_text(encoding="utf-8")
    assert proc.stdout == ""
