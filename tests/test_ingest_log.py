"""scripts/ingest_log.py --reset is all-or-nothing (P65).

Run as a subprocess against a throwaway DB file: the script is a separate
process in real use, and a rejected row must leave the stored sessions alone.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parent.parent
HEADER = "| Exercise | Weight | Rest | Set | Reps | RPE | Notes |\n|---|---|---|---|---|---|---|\n"


def _log(*days: tuple[str, list[str]]) -> str:
    out = "## January 2024\n\n"
    for d, rows in days:
        out += f"### {d}\n\n{HEADER}" + "\n".join(rows) + "\n\n"
    return out


GOOD = _log(("01/08", ["| Squat | 100kg | 3 | 1 | 5 | 8 | |"]),
            ("01/10", ["| Bench Press | 80kg | 3 | 1 | 5 | 8 | |"]))
BAD = _log(("01/08", ["| Squat | 100kg | 3 | 1 | 5 | 8 | |"]),
           ("01/10", ["| Pull-Up | 0kg | 3 | 1 | 0 | 8 | failed rep |"]))
SMALL = _log(("01/15", ["| Deadlift | 140kg | 3 | 1 | 3 | 8 | |"]))


@pytest.fixture
def db(tmp_path):
    from alembic import command
    from alembic.config import Config
    path = tmp_path / "ingest.duckdb"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{path}")
    command.upgrade(cfg, "head")
    return path


def _ingest(db, tmp_path, text, *flags):
    f = tmp_path / "log.md"
    f.write_text(text, encoding="utf-8")
    env = {**os.environ, "GYM_COACH_DUCKDB": str(db)}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "ingest_log.py"), *flags, str(f)],
                          capture_output=True, text=True, env=env, cwd=ROOT)


def _query(db, sql):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_reset_with_a_rejected_row_deletes_and_writes_nothing(db, tmp_path):
    assert _ingest(db, tmp_path, GOOD).returncode == 0
    assert _query(db, "SELECT count(*) FROM sessions")[0][0] == 2
    r = _ingest(db, tmp_path, BAD, "--reset")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert r.stderr.strip()  # a clear error, not silence
    assert _query(db, "SELECT count(*) FROM sessions")[0][0] == 2
    assert _query(db, "SELECT count(*) FROM decision_log")[0][0] == 0


def test_plain_ingest_with_a_rejected_row_writes_nothing(db, tmp_path):
    r = _ingest(db, tmp_path, BAD)
    assert r.returncode == 1
    assert _query(db, "SELECT count(*) FROM sessions")[0][0] == 0


def test_reset_replaces_sessions_and_audits_the_counts(db, tmp_path):
    assert _ingest(db, tmp_path, GOOD).returncode == 0
    r = _ingest(db, tmp_path, SMALL, "--reset")
    assert r.returncode == 0, r.stderr
    assert _query(db, "SELECT count(*) FROM sessions")[0][0] == 1
    rows = _query(db, "SELECT event_type, payload FROM decision_log")
    assert len(rows) == 1
    payload = json.loads(rows[0][1])
    assert payload["deleted"] == 2 and payload["ingested"] == 1
