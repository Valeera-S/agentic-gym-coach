"""Crash evidence for the MCP stdio server (P73).

The MCP client (Claude Code) keeps no server stderr, so when the process died
mid-call ("Connection closed") nothing said why. `install()` points
`faulthandler` (native crashes: SIGSEGV, SIGABRT, Windows access violations,
a hard stack overflow) and a timestamped uncaught-exception hook at
`mcp_server_crash.log`, in the directory of the DuckDB file (GYM_COACH_DUCKDB,
default `<repo>/data/`, which is git-ignored; tests get their temp dir).

Never writes to stdout: that is the MCP protocol channel.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

LOG_NAME = "mcp_server_crash.log"
_REPO_ROOT = Path(__file__).resolve().parent
_fh = None  # faulthandler needs the file object kept open for the process lifetime


def log_path() -> Path:
    db = os.environ.get("GYM_COACH_DUCKDB")
    base = Path(db).parent if db else _REPO_ROOT / "data"
    return base / LOG_NAME


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write(text: str) -> None:
    if _fh is not None:
        _fh.write(text)
        _fh.flush()


def install() -> Path | None:
    """Enable the crash log; returns its path, or None when it cannot be opened
    (logging must never stop the server from starting)."""
    global _fh
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _fh = open(path, "a", encoding="utf-8", buffering=1)
        _fh.write(f"\n=== {_stamp()} server start pid={os.getpid()} ===\n")
        _fh.flush()
        faulthandler.enable(file=_fh, all_threads=True)
    except OSError:
        _fh = None
        return None

    default_hook = sys.excepthook
    default_thread_hook = threading.excepthook

    def _excepthook(etype, value, tb):
        _write(f"\n=== {_stamp()} uncaught exception (pid={os.getpid()}) ===\n"
               + "".join(traceback.format_exception(etype, value, tb)))
        default_hook(etype, value, tb)

    def _thread_hook(args):
        _write(f"\n=== {_stamp()} uncaught exception in thread "
               f"{getattr(args.thread, 'name', '?')} (pid={os.getpid()}) ===\n"
               + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
        default_thread_hook(args)

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook
    return path
