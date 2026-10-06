"""phase — the single current-phase resolver (audit T3: was 3 divergent copies).

P45: current phase = the phase of the most recent TRAINING session; the latest
phase_snapshots row is only the fallback when there are no training sessions
(the snapshot writes current_phase() back, so reading it first froze the phase).
"""

from datetime import date

from models import ExerciseModel, PhaseType, SessionInput
from skills.init import get_duckdb
from skills.phase import current_phase, phase_for_logging
from skills.session_logger import log_session
from skills.snapshot import generate_phase_snapshot

D = date(2030, 3, 1)


def _log(d: date, phase: PhaseType | None = None, kind: str = "training") -> None:
    ex = ExerciseModel(name="Squat", sets=1, reps=[5], rpe=[8], weight_kg=[100.0])
    log_session(SessionInput(date=d, exercises=[ex], phase=phase, kind=kind))


def test_no_data_returns_none():
    assert current_phase() is None


def test_most_recent_training_session_decides_the_phase():
    _log(D, PhaseType.cut)
    _log(D, PhaseType.cut)
    _log(date(2030, 3, 2), PhaseType.accumulation)   # fewer sessions, but latest
    assert current_phase() is PhaseType.accumulation


def test_same_date_sessions_resolve_by_creation_order():
    _log(D, PhaseType.accumulation)
    _log(D, PhaseType.cut)                            # created last
    assert current_phase() is PhaseType.cut


def test_phase_is_not_frozen_by_a_snapshot():
    """P45 repro: lean_bulk, snapshot, three cut sessions, snapshot -> cut."""
    today = date.today()
    _log(today, PhaseType.lean_bulk)
    generate_phase_snapshot()
    assert current_phase() is PhaseType.lean_bulk
    for _ in range(3):
        _log(today, PhaseType.cut)
    assert current_phase() is PhaseType.cut
    assert generate_phase_snapshot().phase is PhaseType.cut


def test_untagged_session_inherits_the_latest_session_phase_not_a_stale_snapshot():
    today = date.today()
    _log(today, PhaseType.lean_bulk)
    generate_phase_snapshot()
    _log(today, PhaseType.cut)
    _log(today)                                       # untagged
    assert get_duckdb().execute(
        "SELECT phase FROM sessions ORDER BY created_at DESC, id LIMIT 1"
    ).fetchone()[0] == "cut"


def test_backfilling_an_older_session_does_not_change_the_phase():
    _log(date(2030, 3, 10), PhaseType.cut)
    _log(date(2030, 2, 1), PhaseType.lean_bulk)       # logged later, dated earlier
    assert current_phase() is PhaseType.cut


def test_habit_sessions_never_decide_the_phase():
    _log(D, PhaseType.accumulation)
    _log(date(2030, 3, 5), PhaseType.deload, kind="habit")
    assert current_phase() is PhaseType.accumulation


def test_snapshot_phase_is_only_the_fallback_without_training_sessions():
    get_duckdb().execute(
        "INSERT INTO phase_snapshots (snapshot_date, phase) VALUES (?, ?)",
        [date(2030, 3, 2), "accumulation"],
    )
    assert current_phase() is PhaseType.accumulation
    _log(D, PhaseType.cut, kind="habit")              # habits do not displace it
    assert current_phase() is PhaseType.accumulation
    _log(D, PhaseType.cut)                            # a training session does
    assert current_phase() is PhaseType.cut


def test_phase_for_logging_defaults_to_maintenance():
    assert phase_for_logging() is PhaseType.maintenance


def test_phase_for_logging_explicit_wins():
    assert phase_for_logging(PhaseType.deload) is PhaseType.deload


def test_phase_for_logging_uses_current_phase():
    _log(D, PhaseType.cut)
    assert phase_for_logging() is PhaseType.cut
