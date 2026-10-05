"""DB connection helpers — single source for DuckDB + LanceDB handles.

Persistent file mode (SPEC §1.1):
  - DuckDB: data/gym_coach.duckdb
  - LanceDB: data/lance_db/ (deferred — v2 Tier 3 uses DuckDB tables)

Skills call get_duckdb()/get_lance() instead of opening their own — avoids
locking fights and keeps the close path in one place. Within one unit of work
every get_duckdb() returns the same cached connection.

Connection lifecycle: DuckDB allows one read-write process per file, and an
open connection holds that lock. A long-lived process (the MCP server) that
kept its connection between tool calls locked every other process — alembic,
scripts/ingest_log.py, the CLI — out of the DB, even while it sat idle. So a
tool call runs inside `connection_scope()`: the connection is opened on first
use and released when the outermost scope exits. Code outside any scope
(tests, one-shot scripts) keeps the old open-until-close_all() behaviour.

`lancedb` is imported lazily inside get_lance(): nothing in v2 calls it, and
its ~1.8s import cost would otherwise tax every CLI invocation against the
0.5s per-call budget (perf bench 2026-09-17).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

# ponytail: process-global singletons. DB file is single-writer anyway,
# so parallel connections buy nothing here. Swap for a pool if concurrent
# writers ever show up.
_duck: duckdb.DuckDBPyConnection | None = None
_lance: Any = None  # lancedb.LanceDBConnection — typed loosely for import cheapness

# Open connection_scope() count. The connection is released only when the
# OUTERMOST scope exits, so a handler that calls another scoped helper never
# has its connection closed underneath it. The re-entrant lock serializes
# scopes across threads (an MCP runtime may run sync tools on worker threads).
_scope_depth = 0
_scope_lock = threading.RLock()

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DUCK_PATH = Path(os.environ.get("GYM_COACH_DUCKDB", _REPO_ROOT / "data" / "gym_coach.duckdb"))
_LANCE_PATH = Path(os.environ.get("GYM_COACH_LANCE", _REPO_ROOT / "data" / "lance_db"))


def get_duckdb() -> duckdb.DuckDBPyConnection:
    """Return the shared DuckDB connection (creates the file on first call).

    A file locked by another process raises duckdb.IOException with a detail
    that says so — still a DuckDB error, so the tool contract reports it as
    `db`. No retry: a genuine collision is halt-and-report.
    """
    global _duck
    with _scope_lock:
        if _duck is None:
            _DUCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            try:
                _duck = duckdb.connect(str(_DUCK_PATH))
            except duckdb.IOException as e:
                # Only a lock conflict is worth a "retry later"; any other IO
                # failure (not a DuckDB file, a directory, no permission) will
                # never clear by waiting, so it passes through unchanged.
                if not _is_lock_conflict(e):
                    raise
                raise duckdb.IOException(
                    f"database file {_DUCK_PATH} is in use by another process "
                    "(DuckDB allows a single read-write process; retry once that "
                    f"process has released it). Underlying error: {e}"
                ) from e
        return _duck


# DuckDB's lock-conflict wording: Windows reports the file as already open in
# another process (when the holder is still alive for DuckDB to name), POSIX
# reports a conflicting lock. A collision with a very short-lived holder on
# Windows can instead surface as the OS's own (localized) sharing-violation
# text; that passes through unrelabeled — still a `db` error, still accurate —
# rather than matching localized strings and mislabeling unrelated errors.
_LOCK_CONFLICT_MARKERS = ("file is already open in", "could not set lock on file",
                          "conflicting lock is held")


def _is_lock_conflict(e: Exception) -> bool:
    msg = str(e).lower()
    return any(marker in msg for marker in _LOCK_CONFLICT_MARKERS)


def _close_duckdb() -> None:
    global _duck
    if _duck is not None:
        try:
            _duck.close()
        finally:
            _duck = None


@contextmanager
def connection_scope() -> Iterator[None]:
    """Hold the DuckDB connection for one unit of work, then release the file.

    Re-entrant: nested scopes share the connection; only the outermost exit
    closes it (also on error). Opening is lazy — a scope that never touches
    the DB never opens it.

    The scope also holds `_scope_lock` for its whole duration, so units of
    work from different threads run one at a time. A DuckDB connection is not
    safe to share across concurrently running threads, and the file is
    single-writer anyway — serializing ~15ms tool calls costs nothing real.
    """
    global _scope_depth
    with _scope_lock:
        _scope_depth += 1
        try:
            yield
        finally:
            _scope_depth -= 1
            if _scope_depth == 0:
                _close_duckdb()


def get_lance() -> Any:
    """Return the shared LanceDB connection (creates the dir on first call)."""
    global _lance
    if _lance is None:
        import lancedb  # lazy: ~1.8s import, unused by any v2 skill

        _LANCE_PATH.mkdir(parents=True, exist_ok=True)
        _lance = lancedb.connect(str(_LANCE_PATH))
    return _lance


def close_all() -> None:
    """Close shared connections — call at process exit / tests teardown."""
    global _lance
    with _scope_lock:
        _close_duckdb()
    _lance = None
