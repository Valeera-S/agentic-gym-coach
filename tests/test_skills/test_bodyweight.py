"""bodyweight log (P30): input validation, per-condition averages, window
semantics, snapshot hook, migration 0005, and both tool surfaces."""

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

import coach_tools
import mcp_server
from models.bodyweight import BodyweightInput, WeighCondition
from skills.bodyweight import bodyweight_history, list_readings, log_bodyweight, summarize
from skills.init import get_duckdb
from skills.snapshot import generate_phase_snapshot

_REPO = Path(__file__).resolve().parent.parent.parent
TODAY = date.today()


def _log(days_ago, kg, cond="morning_fasted", **kw):
    return log_bodyweight(BodyweightInput(
        date=TODAY - timedelta(days=days_ago), weight_kg=kg, condition=cond, **kw))


def _count():
    return get_duckdb().execute("SELECT count(*) FROM bodyweight_log").fetchone()[0]


# --- input ---------------------------------------------------------------

def test_kg_round_trip_exact():
    r = log_bodyweight(BodyweightInput(date=TODAY, weight=72.45, unit="kg",
                                       condition="fed", scale="test scale", notes="1h after meal"))
    assert r.weight_kg == 72.45 and r.weight_entered == 72.45 and r.weight_unit.value == "kg"
    (back,) = list_readings(TODAY, TODAY)
    assert back == r and back.condition is WeighCondition.fed and back.scale == "test scale"


def test_lb_input_converts_exactly_and_keeps_entered_values():
    r = log_bodyweight(BodyweightInput(date=TODAY, weight=150, unit="lb", condition="post_workout"))
    assert r.weight_kg == 150 * 0.45359237
    assert (r.weight_entered, r.weight_unit.value) == (150, "lb")


def test_legacy_weight_kg_only_has_no_entered_fields():
    r = _log(0, 74.15, "post_workout")
    assert r.weight_kg == 74.15 and r.weight_entered is None and r.weight_unit is None


def test_weight_kg_and_weight_agreeing_is_accepted_disagreeing_rejected():
    BodyweightInput(date=TODAY, weight=100, unit="kg", weight_kg=100, condition="fed")
    with pytest.raises(ValidationError, match="not both"):
        BodyweightInput(date=TODAY, weight=100, unit="kg", weight_kg=90, condition="fed")


@pytest.mark.parametrize("kw", [
    dict(weight_kg=0), dict(weight_kg=-1), dict(weight_kg=400.01), dict(weight_kg=float("nan")),
    dict(weight=1000, unit="lb"),          # 453.6 kg
    dict(weight=70),                        # no unit
    dict(weight=70, unit="stone"),          # bad unit
    dict(unit="kg"),                        # unit without weight
    dict(),                                 # no weight at all
])
def test_bad_weights_rejected_before_any_write(kw):
    with pytest.raises(ValidationError):
        BodyweightInput(date=TODAY, condition="fed", **kw)
    with pytest.raises((ValueError, TypeError)):
        coach_tools.DISPATCH["bodyweight_log"]({"date": TODAY.isoformat(), "condition": "fed", **kw})
    assert _count() == 0


def test_bound_edge_400_accepted():
    assert _log(0, 400.0).weight_kg == 400.0


@pytest.mark.parametrize("cond", ["breakfast", "", None, "FED"])
def test_bad_or_missing_condition_rejected(cond):
    with pytest.raises(ValidationError):
        BodyweightInput(date=TODAY, weight_kg=70, condition=cond)
    assert _count() == 0


def test_condition_is_required():
    with pytest.raises(ValidationError):
        BodyweightInput(date=TODAY, weight_kg=70)


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        BodyweightInput(date=TODAY, weight_kg=70, condition="fed", bogus=1)


def test_several_readings_per_date_allowed():
    _log(0, 66.0, "morning_fasted")
    _log(0, 67.0, "post_workout")
    assert _count() == 2


def test_log_does_not_touch_profile():
    from skills.profile import get_profile
    _log(0, 70.0)
    assert get_profile() is None


# --- averages / windows ----------------------------------------------------

def test_per_condition_averages_never_mix():
    _log(1, 66.0, "morning_fasted")
    _log(2, 67.0, "morning_fasted")
    _log(1, 70.0, "post_workout")
    _log(1, 68.0, "fed")
    s = {a.condition.value: a for a in summarize(TODAY - timedelta(days=6), TODAY).averages}
    assert set(s) == {"morning_fasted", "post_workout", "fed"}
    assert (s["morning_fasted"].mean_kg, s["morning_fasted"].readings) == (66.5, 2)
    assert (s["post_workout"].mean_kg, s["post_workout"].readings) == (70.0, 1)
    assert (s["fed"].mean_kg, s["fed"].readings) == (68.0, 1)


def test_window_boundaries_day_n_minus_1_in_day_n_out():
    _log(6, 60.0)   # 7-day window ending today: today-6 is the first day
    _log(7, 99.0)   # today-7 is outside
    _log(0, 62.0)
    h = bodyweight_history(28, TODAY)
    (avg,) = h.last_7_days.averages
    assert avg.readings == 2 and avg.mean_kg == 61.0
    # whole 28-day window: day 27 in, day 28 out
    _log(27, 50.0)
    _log(28, 40.0)
    h = bodyweight_history(28, TODAY)
    assert h.start == TODAY - timedelta(days=27)
    assert sorted(r.weight_kg for r in h.readings) == [50.0, 60.0, 62.0, 99.0]
    (avg,) = h.window.averages
    assert avg.readings == 4


def test_history_end_date_anchors_window():
    _log(10, 65.0)
    h = bodyweight_history(3, TODAY - timedelta(days=9))
    assert [r.weight_kg for r in h.readings] == [65.0]
    assert bodyweight_history(3, TODAY).readings == []


def test_history_empty():
    h = bodyweight_history(28, TODAY)
    assert h.readings == [] and h.window.averages == [] and h.last_7_days.averages == []


# --- snapshot --------------------------------------------------------------

def test_snapshot_is_null_without_fasted_readings():
    _log(0, 70.0, "fed")
    _log(1, 71.0, "post_workout")
    _log(2, 72.0, "unknown")
    assert generate_phase_snapshot().body_weight_kg is None
    stored = get_duckdb().execute("SELECT body_weight_kg FROM phase_snapshots").fetchone()[0]
    assert stored is None


def test_snapshot_does_not_fall_back_to_profile():
    from models import UserProfile
    from skills.profile import set_profile
    set_profile(UserProfile(bodyweight_kg=80.0))
    assert generate_phase_snapshot().body_weight_kg is None


def test_snapshot_uses_only_fasted_7_day_mean():
    _log(0, 66.0)
    _log(6, 67.0)
    _log(7, 90.0)                 # outside 7 days
    _log(0, 80.0, "post_workout")  # other condition ignored
    snap = generate_phase_snapshot()
    assert snap.body_weight_kg == 66.5
    stored = get_duckdb().execute("SELECT body_weight_kg FROM phase_snapshots").fetchone()[0]
    assert float(stored) == 66.5


# --- migration 0005 ----------------------------------------------------------

def _cfg(db):
    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{db}")
    return cfg


def test_upgrade_adds_table_and_leaves_sessions_untouched(tmp_path):
    db = tmp_path / "m.duckdb"
    command.upgrade(_cfg(db), "0004_decision_payload")
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO sessions (date, kind) VALUES ('2026-10-01', 'training')")
    before = con.execute("SELECT * FROM sessions").fetchall()
    con.close()
    command.upgrade(_cfg(db), "0005_bodyweight_log")
    con = duckdb.connect(str(db))
    after = con.execute("SELECT * FROM sessions").fetchall()
    cols = {r[0] for r in con.execute("DESCRIBE bodyweight_log").fetchall()}
    con.close()
    assert after == before and len(after) == 1
    assert cols == {"id", "date", "weight_kg", "weight_entered", "weight_unit",
                    "condition", "scale", "notes", "created_at"}


def test_downgrade_refuses_when_rows_exist_then_drops_when_empty(tmp_path):
    db = tmp_path / "d.duckdb"
    command.upgrade(_cfg(db), "0005_bodyweight_log")
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO bodyweight_log (date, weight_kg, condition) "
                "VALUES ('2026-10-06', 72.45, 'fed')")
    con.close()
    with pytest.raises(RuntimeError, match="refusing to downgrade 0005"):
        command.downgrade(_cfg(db), "0004_decision_payload")
    con = duckdb.connect(str(db))
    assert con.execute("SELECT count(*) FROM bodyweight_log").fetchone()[0] == 1
    con.execute("DELETE FROM bodyweight_log")
    con.close()
    command.downgrade(_cfg(db), "0004_decision_payload")
    con = duckdb.connect(str(db))
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert "bodyweight_log" not in tables


# --- tool surfaces -----------------------------------------------------------

def test_handlers_log_and_history_round_trip():
    out = coach_tools.DISPATCH["bodyweight_log"](
        {"date": "2026-09-21", "weight": 148.5, "unit": "lb", "condition": "post_workout"})
    assert out["condition"] == "post_workout" and out["weight_unit"] == "lb"
    h = coach_tools.DISPATCH["bodyweight_history"]({"window_days": 5, "end_date": "2026-09-21"})
    assert len(h["readings"]) == 1 and h["window"]["averages"][0]["readings"] == 1


def test_handler_history_rejects_bad_window():
    with pytest.raises(ValueError):
        coach_tools.DISPATCH["bodyweight_history"]({"window_days": 0})
    with pytest.raises(ValueError):
        coach_tools.DISPATCH["bodyweight_history"]({"window_days": "28"})


def test_mcp_wrappers():
    r = mcp_server.coach_bodyweight_log(date=TODAY.isoformat(), condition="fed", weight=72.45, unit="kg")
    assert "error" not in r and r["weight_kg"] == 72.45
    h = mcp_server.coach_bodyweight_history()
    assert h["window_days"] == 28 and len(h["readings"]) == 1
    bad = mcp_server.coach_bodyweight_log(date=TODAY.isoformat(), condition="lunch", weight_kg=70)
    assert bad["error"] == "invalid_input"
    bad = mcp_server.coach_bodyweight_log(date=TODAY.isoformat(), condition="fed", weight_kg=500)
    assert bad["error"] == "invalid_input"
    assert _count() == 1


def test_cli_subprocess_round_trip():
    from skills.init import close_all

    def run(cmd, payload):
        close_all()  # the test process must not hold the file the CLI opens
        return subprocess.run([sys.executable, str(_REPO / "coach_tools.py"), cmd, json.dumps(payload)],
                              capture_output=True, text=True, cwd=_REPO)
    p = run("bodyweight_log", {"date": "2026-10-06", "weight_kg": 72.45, "condition": "fed"})
    assert p.returncode == 0, p.stderr
    p = run("bodyweight_history", {"window_days": 3, "end_date": "2026-10-06"})
    assert p.returncode == 0 and len(json.loads(p.stdout)["readings"]) == 1
    p = run("bodyweight_log", {"date": "2026-10-06", "weight_kg": 72.45})
    assert p.returncode == 1 and json.loads(p.stdout)["error"] == "invalid_input"
