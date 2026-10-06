"""ingest_log.py — parse log.md into sessions and load them into DuckDB.

The user logs raw markdown tables with minimal effort. This script turns
that into SessionInput objects (canonicalizing exercises, carrying RPE
forward, deriving pain_flag from notes) and calls session_logger.log_session()
once per session. Quirks handled (AGENTS.md `log.md format gotchas`):
  - Date `MM/DD`; year taken from the preceding `## <Month> <Year>` header
  - Weight: `32kg`, `58.4kg`, `5.4kg (12lb)`, `45kg→40kg` (dropset → top load),
    `—` (bodyweight/unrecorded → None)
  - RPE 1-10, `—` = unrecorded (None), blank = carried forward from prior set
  - Reps may be half (`4.5`); `—` = unrecorded (None)
  - Consecutive same-name rows = one exercise w/ multiple sets
  - pain_flag derived from notes keywords (pain/uncomfortable/sore/injury/...)
  - Two `### MM/DD` headers same day (Session A/B) → two session rows

Run:  python scripts/ingest_log.py            (ingests log.md)
      python scripts/ingest_log.py --dry-run (parse only, no DB writes)
      python scripts/ingest_log.py --reset   (replace ALL stored sessions; the whole file is
                                              validated first and the swap is one transaction,
                                              audited in decision_log as `sessions_reset`)
      python scripts/ingest_log.py --demo    (self-check: parse sample log, no DB writes)
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from models import ExerciseModel, SessionInput  # noqa: E402
from skills.session_logger import log_session  # noqa: E402

_DATE_HEADER = re.compile(r"^###\s*(\d{2})/(\d{2})(?:\s*\(.*?\))?\s*$")
_MONTH_HEADER = re.compile(r"^##\s*([A-Za-z]+)\s+(\d{4})\s*$")
_WEIGHT_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*kg", re.IGNORECASE)
_PAIN_KEYS = ("pain", "uncomfortable", "uncomf", "sore", "injury", "tendon", "hurt", "twinge")


def _parse_weight(s: str) -> float | None:
    s = s.strip()
    if s in ("", "—", "-"):
        return None
    m = _WEIGHT_NUM.search(s)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _parse_reps(s: str) -> float | None:
    s = s.strip()
    if s in ("", "—", "-"):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _parse_rpe(s: str, prev: float | None) -> float | None:
    s = s.strip()
    if s == "—":
        return None
    if s == "":
        return prev  # carry forward
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else prev


def _split_row(line: str) -> list[str]:
    # Strip outer pipes; split into at most 7 cells so Notes keeps any "|".
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|", 6)]


def _pain_flag(notes: str | None) -> bool:
    if not notes:
        return False
    low = notes.lower()
    return any(k in low for k in _PAIN_KEYS)


def _build_exercises(blocks: list[tuple[str, list[list[str]]]]) -> list[ExerciseModel]:
    out: list[ExerciseModel] = []
    for name, rows in blocks:
        reps = [_parse_reps(r[4]) for r in rows]
        weight = [_parse_weight(r[1]) for r in rows]
        rpe: list[float | None] = []
        prev: float | None = None
        for r in rows:
            v = _parse_rpe(r[5], prev)
            rpe.append(v)
            if v is not None:
                prev = v
        note_parts = [r[6].strip() for r in rows if r[6].strip()]
        notes = " / ".join(note_parts) or None
        pain = _pain_flag(notes)
        # muscle_group is left to log_session, which classifies the name and
        # records how (catalog / keyword / unclassified). Passing the catalog's
        # guess here would store it as caller-set.
        out.append(
            ExerciseModel(
                name=name, sets=len(rows), reps=reps, rpe=rpe,
                weight_kg=weight, notes=notes, pain_flag=pain,
            )
        )
    return out


def parse_log_md(text: str) -> list[SessionInput]:
    lines = text.splitlines()
    sessions: list[SessionInput] = []
    year: int | None = None
    cur_date: date | None = None
    cur_blocks: list[tuple[str, list[list[str]]]] = []
    cur_rows: list[list[str]] = []
    cur_name: str | None = None

    def flush_exercise():
        nonlocal cur_rows, cur_name
        if cur_name and cur_rows:
            cur_blocks.append((cur_name, cur_rows))
        cur_rows = []
        cur_name = None

    def flush_session():
        nonlocal cur_date, cur_blocks
        if cur_date is not None and cur_blocks:  # a date with no exercises is no session (P39)
            sessions.append(SessionInput(date=cur_date, exercises=_build_exercises(cur_blocks)))
        cur_date = None
        cur_blocks = []

    for line in lines:
        mh = _MONTH_HEADER.match(line)
        if mh:
            flush_exercise()
            flush_session()
            year = int(mh.group(2))
            continue
        dh = _DATE_HEADER.match(line)
        if dh:
            flush_exercise()
            flush_session()
            mm, dd = int(dh.group(1)), int(dh.group(2))
            cur_date = date(year, mm, dd) if year else None
            continue
        if line.strip().startswith(">"):  # blockquote note lines
            continue
        if line.startswith("|") and cur_date is not None:
            cells = _split_row(line)
            if len(cells) < 6:
                continue
            if cells[0].lower() == "exercise":  # table header row
                continue
            if all(set(c) <= {"-", " "} for c in cells):  # separator row |---|---|
                continue
            name = cells[0]
            if not name:
                continue
            if cur_name and name != cur_name:
                flush_exercise()
            cur_name = name
            cur_rows.append(cells)
            continue
    flush_exercise()
    flush_session()
    return sessions


def ingest(path: Path, dry_run: bool = False, reset: bool = False) -> int:
    """Parse and validate the WHOLE file first (parse_log_md builds every
    validated SessionInput); only then, in ONE transaction, delete (reset) and
    insert everything. Any failure rolls back: nothing deleted, nothing written
    (P65). A reset writes one decision_log entry with the counts."""
    text = path.read_text(encoding="utf-8")
    sessions = parse_log_md(text)
    if dry_run:
        for s in sessions:
            print(f"  would log {s.date}  exercises={len(s.exercises)}")
        return len(sessions)

    import json
    from models import DecisionEventType
    from skills.init import get_duckdb
    from skills.sessions import _in_transaction

    def work() -> int:
        d = get_duckdb()
        deleted = 0
        if reset:
            deleted = d.execute("SELECT count(*) FROM sessions").fetchone()[0]
            d.execute("DELETE FROM sessions")
        for s in sessions:
            log_session(s)
        if reset:
            d.execute(
                """INSERT INTO decision_log (event_type, trigger_signal, reasoning_chain, payload)
                   VALUES (?, ?, ?, ?)""",
                [DecisionEventType.sessions_reset.value,
                 f"ingest_log --reset {path.name}",
                 f"reset ingest: deleted {deleted} session(s), ingested {len(sessions)}",
                 json.dumps({"deleted": deleted, "ingested": len(sessions),
                             "source": str(path)}, ensure_ascii=False)])
        return len(sessions)

    return _in_transaction(work)


def _demo() -> int:
    """Self-check — parses the sample log deterministically, no DB writes."""
    n = ingest(_REPO_ROOT / "docs" / "reference" / "sample_log.md", dry_run=True)
    assert n > 10, f"expected many sessions, got {n}"
    print(f"OK: parsed {n} sessions")
    return 0


def main(argv: list[str]) -> int:
    if "--demo" in argv:
        return _demo()
    dry = "--dry-run" in argv
    reset = "--reset" in argv
    pos = [a for a in argv if not a.startswith("--")]
    path = (Path(pos[0]) if pos else _REPO_ROOT / "docs" / "reference" / "sample_log.md")
    if not path.exists():
        print(f"log file not found at {path}", file=sys.stderr)
        return 1
    try:
        n = ingest(path, dry_run=dry, reset=reset and not dry)
    except Exception as e:  # nothing was deleted or written: say so, exit 1
        print(f"ingest failed, nothing was changed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"{'parsed' if dry else 'ingested'} {n} sessions"
          + (" (sessions table reset first)" if reset and not dry else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))