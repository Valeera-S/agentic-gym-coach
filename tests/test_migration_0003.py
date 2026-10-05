"""Migration 0003 — muscle vocabulary to VARCHAR, new per-exercise fields, kind.

Upgrade/downgrade cases run on their own scratch DB under tmp_path, seeded at
revision 0002 with rows shaped like real usage. The vocabulary round-trip
cases use the suite's shared test DB (already at head).
"""

from __future__ import annotations

import json
from datetime import date

import duckdb
import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from models import ExerciseModel, MuscleGroup, SessionInput, UserProfile
from skills.init import get_duckdb

_REPO_INI = "alembic.ini"

# Shaped like the real 2026-09-19 push day: an upper_chest primary, per-hand
# lb-converted weights, a bodyweight exercise with null weights + null reps.
_LEGACY_EXERCISES = [
    {"name": "Dumbbell Bench Press", "muscle_group": "upper_chest", "sets": 4,
     "reps": [10.0, 10.0, 9.0, 8.0], "rpe": [8.0, 8.5, 9.0, None],
     "weight_kg": [11.34, 11.34, 11.34, 9.07], "tempo": None,
     "form_quality": 5, "pain_flag": False, "notes": "25 lb per hand"},
    {"name": "Dip", "muscle_group": "triceps", "sets": 3,
     "reps": [None, None, None], "rpe": [None, None, None],
     "weight_kg": [None, None, None], "tempo": "3-1-X-1",
     "form_quality": 2, "pain_flag": True, "notes": None},
    {"name": "Crunch", "muscle_group": "core", "sets": 2,
     "reps": [15.0, 15.0], "rpe": [7.0, 7.0], "weight_kg": [None, None],
     "tempo": None, "form_quality": 5, "pain_flag": False, "notes": None},
]


def _cfg(db) -> Config:
    cfg = Config(_REPO_INI)
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{db}")
    return cfg


def _current_revision(db) -> str:
    con = duckdb.connect(str(db))
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def legacy_db(tmp_path):
    """A DB at 0002 holding several sessions + a profile, then upgraded."""
    db = tmp_path / "legacy.duckdb"
    command.upgrade(_cfg(db), "0002_profile_memory")
    con = duckdb.connect(str(db))
    con.execute(
        "INSERT INTO sessions (date, phase, pre_recovery_score, exercises, post_feedback) "
        "VALUES (DATE '2026-09-19', 'maintenance', 80, ?, 'good pump')",
        [_LEGACY_EXERCISES],
    )
    con.execute(
        "INSERT INTO sessions (date, phase, exercises) VALUES "
        "(DATE '2026-09-21', 'lean_bulk', ?), (DATE '2026-09-23', 'deload', NULL), "
        "(DATE '2026-09-25', 'maintenance', [])",
        [[_LEGACY_EXERCISES[0]]],
    )
    con.execute("INSERT INTO user_profiles (payload) VALUES (?)", [json.dumps({
        "priority_muscles": ["upper_chest", "side_delt"],
        "goals": [{"kind": "hypertrophy", "target_muscles": ["upper_chest"],
                   "metric": "upper_chest"}],
        "notes": 'I said "upper_chest" once',
    })])
    before = con.execute(
        "SELECT id, date, phase, pre_recovery_score, post_feedback, created_at "
        "FROM sessions ORDER BY date").fetchall()
    con.close()
    command.upgrade(_cfg(db), "head")
    return db, before


def test_upgrade_preserves_every_value_except_the_chest_remap(legacy_db):
    db, before = legacy_db
    con = duckdb.connect(str(db))
    try:
        after = con.execute(
            "SELECT id, date, phase, pre_recovery_score, post_feedback, created_at "
            "FROM sessions ORDER BY date").fetchall()
        assert after == before  # ids, dates, phases, scores, timestamps verbatim

        rows = con.execute("SELECT exercises, kind FROM sessions ORDER BY date").fetchall()
        first, kind = rows[0]
        assert kind == "training"
        assert [e["muscle_group"] for e in first] == ["chest", "triceps", "core"]
        for got, want in zip(first, _LEGACY_EXERCISES):
            for field in ("name", "sets", "reps", "rpe", "tempo",
                          "form_quality", "pain_flag", "notes"):
                assert got[field] == want[field], field
        # legacy float32 weights come back as the decimals that were stored
        assert first[0]["weight_kg"] == [11.34, 11.34, 11.34, 9.07]
        assert first[1]["weight_kg"] == [None, None, None]  # bodyweight stays null
        # new fields are NULL = "unknown, pre-0003"
        for e in first:
            for field in ("raw_name", "muscle_source", "load_type",
                          "entered_weight", "entered_unit"):
                assert e[field] is None
        assert rows[1][0][0]["muscle_group"] == "chest"
        assert rows[2][0] is None and rows[3][0] == []  # null / empty lists survive
        assert {k for _, k in rows} == {"training"}
    finally:
        con.close()


def test_upgrade_remaps_profile_vocabulary_but_not_prose(legacy_db):
    db, _ = legacy_db
    con = duckdb.connect(str(db))
    payload = json.loads(con.execute("SELECT payload FROM user_profiles").fetchone()[0])
    con.close()
    assert payload["priority_muscles"] == ["chest", "side_delt"]
    assert payload["goals"][0]["target_muscles"] == ["chest"]
    # only muscle-typed paths are remapped: free text is never rewritten
    assert payload["goals"][0]["metric"] == "upper_chest"
    assert payload["notes"] == 'I said "upper_chest" once'


def test_muscle_group_db_enum_type_is_gone(legacy_db):
    db, _ = legacy_db
    con = duckdb.connect(str(db))
    try:
        assert con.execute(
            "SELECT count(*) FROM duckdb_types() WHERE type_name = 'muscle_group'"
        ).fetchone()[0] == 0
        # The DB layer no longer gates the vocabulary: a value outside every old
        # enum is accepted (Pydantic is the only gate).
        con.execute("INSERT INTO sessions (date, phase, exercises) VALUES "
                    "(DATE '2026-10-01', 'maintenance', [{'name': 'x', 'muscle_group': 'not_in_any_enum', 'sets': 1}])")
        con.execute("DELETE FROM sessions WHERE date = DATE '2026-10-01'")
        # constraints survived the table rebuild
        with pytest.raises(duckdb.ConstraintException):
            con.execute("INSERT INTO sessions (date, phase, pre_recovery_score) "
                        "VALUES (DATE '2026-10-01', 'maintenance', 101)")
        with pytest.raises(duckdb.ConstraintException):
            con.execute("INSERT INTO sessions (phase) VALUES ('maintenance')")
    finally:
        con.close()


def test_downgrade_refuses_unrepresentable_data_without_changing_anything(legacy_db):
    db, _ = legacy_db
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO sessions (date, phase, exercises, kind) VALUES "
                "(DATE '2026-10-02', 'maintenance', [{'name': 'Shoulder Press', 'muscle_group': 'front_delt', 'sets': 3}], 'habit')")
    snapshot = con.execute("SELECT * FROM sessions ORDER BY date").fetchall()
    con.close()

    with pytest.raises(RuntimeError) as exc:
        command.downgrade(_cfg(db), "0002_profile_memory")
    msg = str(exc.value)
    assert "front_delt (1)" in msg and "1 non-training session(s)" in msg

    assert _current_revision(db) == "0003_vocab_exercise_fields"
    con = duckdb.connect(str(db))
    assert con.execute("SELECT * FROM sessions ORDER BY date").fetchall() == snapshot
    con.close()


def test_downgrade_refuses_new_vocabulary_in_profiles(legacy_db):
    db, _ = legacy_db
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO user_profiles (payload) VALUES (?)",
                [json.dumps({"priority_muscles": ["erectors"]})])
    con.close()
    with pytest.raises(RuntimeError, match="erectors"):
        command.downgrade(_cfg(db), "0002_profile_memory")
    assert _current_revision(db) == "0003_vocab_exercise_fields"


def test_downgrade_round_trips_when_representable(legacy_db):
    db, before = legacy_db
    command.downgrade(_cfg(db), "0002_profile_memory")
    con = duckdb.connect(str(db))
    first = con.execute("SELECT exercises FROM sessions ORDER BY date").fetchone()[0]
    assert [e["muscle_group"] for e in first] == ["upper_chest", "triceps", "core"]
    payload = json.loads(con.execute("SELECT payload FROM user_profiles").fetchone()[0])
    assert payload["priority_muscles"] == ["upper_chest", "side_delt"]
    con.close()
    command.upgrade(_cfg(db), "head")
    con = duckdb.connect(str(db))
    first = con.execute("SELECT exercises FROM sessions ORDER BY date").fetchone()[0]
    con.close()
    assert first[0]["muscle_group"] == "chest"
    assert first[0]["weight_kg"] == [11.34, 11.34, 11.34, 9.07]


# --- vocabulary through the app (shared test DB, at head) ---------------------

@pytest.mark.parametrize("muscle", ["chest", "front_delt", "erectors", "unclassified",
                                    "core", "serratus"])
def test_new_and_kept_vocabulary_writes_and_reads_back(muscle):
    from skills.session_logger import log_session
    log_session(SessionInput(date=date(2026, 10, 1), exercises=[
        ExerciseModel(name="Some Exercise", muscle_group=muscle, sets=2,
                      reps=[8, 8], weight_kg=[20, 20]),
    ]))
    got = get_duckdb().execute(
        "SELECT exercises[1].muscle_group FROM sessions").fetchone()[0]
    assert got == muscle


def test_retired_upper_chest_spelling_still_means_chest():
    """Every previously valid call keeps its meaning."""
    from coach_tools import DISPATCH
    from skills.session_logger import log_session

    assert MuscleGroup("upper_chest") is MuscleGroup.chest
    log_session(SessionInput(date=date.today(), exercises=[
        ExerciseModel(name="Incline Bench Press", muscle_group="upper_chest",
                      sets=3, reps=[8, 8, 8], weight_kg=[30, 30, 30]),
    ]))
    assert get_duckdb().execute(
        "SELECT exercises[1].muscle_group FROM sessions").fetchone()[0] == "chest"
    report = DISPATCH["trend"]({"muscle": "upper_chest"})
    assert report["muscle"] == "chest" and report["effective_volume"] == 3.0
    assert UserProfile(priority_muscles=["upper_chest"]).priority_muscles == [MuscleGroup.chest]


def test_vocabulary_outside_the_enum_is_rejected_by_pydantic():
    with pytest.raises(ValidationError):
        ExerciseModel(name="x", muscle_group="not_a_muscle", sets=1)
    with pytest.raises(ValidationError):
        ExerciseModel(name="x", muscle_group="Chest", sets=1)  # case-sensitive vocabulary


def test_downgrade_refuses_populated_provenance_fields(legacy_db):
    """A lossy downgrade would silently drop raw_name / muscle_source /
    load_type / the entered weights — refuse instead."""
    db, _ = legacy_db
    con = duckdb.connect(str(db))
    con.execute(
        "INSERT INTO sessions (date, phase, exercises) VALUES (DATE '2026-10-03', 'maintenance', "
        "[{'name': 'Lat Pulldown', 'muscle_group': 'lats', 'sets': 1, 'reps': [10], "
        "'weight_kg': [22.6796185], 'raw_name': 'lat pull down', 'muscle_source': 'catalog', "
        "'load_type': 'machine_stack', 'entered_weight': [50.0], 'entered_unit': 'lb'}])")
    snapshot = con.execute("SELECT * FROM sessions ORDER BY date").fetchall()
    con.close()
    with pytest.raises(RuntimeError, match=r"1 exercise\(s\) carry provenance/load fields"):
        command.downgrade(_cfg(db), "0002_profile_memory")
    assert _current_revision(db) == "0003_vocab_exercise_fields"
    con = duckdb.connect(str(db))
    assert con.execute("SELECT * FROM sessions ORDER BY date").fetchall() == snapshot
    con.close()


def test_downgrade_leaves_free_text_chest_alone(legacy_db):
    db, _ = legacy_db
    con = duckdb.connect(str(db))
    con.execute("DELETE FROM user_profiles")
    con.execute("INSERT INTO user_profiles (payload) VALUES (?)", [json.dumps({
        "priority_muscles": ["chest"],
        "goals": [{"kind": "strength", "metric": "chest", "target_muscles": ["chest", "lats"]}],
    })])
    con.close()
    command.downgrade(_cfg(db), "0002_profile_memory")
    con = duckdb.connect(str(db))
    payload = json.loads(con.execute("SELECT payload FROM user_profiles").fetchone()[0])
    con.close()
    assert payload["priority_muscles"] == ["upper_chest"]
    assert payload["goals"][0]["target_muscles"] == ["upper_chest", "lats"]
    assert payload["goals"][0]["metric"] == "chest"


# --- offline (--sql) mode ------------------------------------------------------

def _render(tmp_path, direction: str, revs: str) -> str:
    import io
    buf = io.StringIO()
    cfg = Config(_REPO_INI, output_buffer=buf)
    cfg.set_main_option("sqlalchemy.url", f"duckdb:///{tmp_path / 'unused.duckdb'}")
    getattr(command, direction)(cfg, revs, sql=True)
    assert not (tmp_path / "unused.duckdb").exists()  # offline never connects
    return buf.getvalue()


def _run_script(db, script: str) -> list[str]:
    """Run a rendered script statement by statement, CONTINUING after errors —
    the way a lenient runner (the DuckDB CLI without -bail) would. Returns the
    error messages."""
    body = [ln for ln in script.splitlines() if not ln.startswith("--")]
    errors = []
    con = duckdb.connect(str(db))
    try:
        for stmt in "\n".join(body).split(";"):
            if not stmt.strip():
                continue
            try:
                con.execute(stmt)
            except duckdb.Error as e:
                errors.append(str(e))
    finally:
        con.close()
    return errors


def _dump(db) -> list:
    con = duckdb.connect(str(db))
    try:
        return [con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall()
                for t in ("sessions", "user_profiles", "alembic_version")]
    finally:
        con.close()


def test_offline_scripts_render_and_are_wrapped_in_a_transaction(tmp_path):
    up = _render(tmp_path, "upgrade", "0002_profile_memory:0003_vocab_exercise_fields")
    down = _render(tmp_path, "downgrade", "0003_vocab_exercise_fields:0002_profile_memory")
    assert "CREATE TABLE sessions_v3" in up and "DROP TYPE muscle_group" in up
    assert "refusing to downgrade 0003" in down
    for script in (up, down):
        assert script.lstrip().startswith("BEGIN") and script.rstrip().endswith("COMMIT;")


@pytest.mark.parametrize("seed", [
    "[{'name': 'x', 'muscle_group': 'erectors', 'sets': 1}], 'training'",
    "[{'name': 'x', 'muscle_group': 'lats', 'sets': 1, 'raw_name': 'lat pull down'}], 'training'",
    "[{'name': 'x', 'muscle_group': 'lats', 'sets': 1}], 'habit'",
])
def test_offline_downgrade_guards_abort_the_whole_step_even_if_the_runner_continues(tmp_path, seed):
    db = tmp_path / "offline.duckdb"
    command.upgrade(_cfg(db), "head")
    con = duckdb.connect(str(db))
    con.execute(f"INSERT INTO sessions (date, phase, exercises, kind) "
                f"VALUES (DATE '2026-10-01', 'maintenance', {seed})")
    con.close()
    before = _dump(db)
    errors = _run_script(db, _render(tmp_path, "downgrade",
                                     "0003_vocab_exercise_fields:0002_profile_memory"))
    assert any("refusing to downgrade 0003" in e for e in errors)
    assert _dump(db) == before  # nothing changed, version included


def test_offline_downgrade_runs_cleanly_on_representable_data(tmp_path):
    db = tmp_path / "offline_ok.duckdb"
    command.upgrade(_cfg(db), "head")
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO sessions (date, phase, exercises) VALUES "
                "(DATE '2026-10-01', 'maintenance', [{'name': 'x', 'muscle_group': 'chest', 'sets': 1}])")
    con.close()
    assert _run_script(db, _render(tmp_path, "downgrade",
                                   "0003_vocab_exercise_fields:0002_profile_memory")) == []
    assert _current_revision(db) == "0002_profile_memory"
    con = duckdb.connect(str(db))
    assert con.execute("SELECT exercises[1].muscle_group FROM sessions").fetchone()[0] == "upper_chest"
    con.close()


def test_offline_upgrade_refuses_profiles_it_cannot_remap_safely(tmp_path):
    """The rendered upgrade cannot do the path-limited profile remap, and a text
    replace would rewrite free text, so it refuses for such a DB."""
    db = tmp_path / "offline_up.duckdb"
    command.upgrade(_cfg(db), "0002_profile_memory")
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO user_profiles (payload) VALUES (?)",
                [json.dumps({"goals": [{"kind": "strength", "metric": "upper_chest"}]})])
    con.close()
    before = _dump(db)
    errors = _run_script(db, _render(tmp_path, "upgrade",
                                     "0002_profile_memory:0003_vocab_exercise_fields"))
    assert any("only the online upgrade can remap" in e for e in errors)
    assert _dump(db) == before


def test_offline_and_online_upgrades_agree_when_offline_is_allowed(tmp_path):
    seed = _LEGACY_EXERCISES
    dbs = {}
    for mode in ("online", "offline"):
        db = tmp_path / f"{mode}.duckdb"
        command.upgrade(_cfg(db), "0002_profile_memory")
        con = duckdb.connect(str(db))
        con.execute("INSERT INTO sessions (id, date, phase, exercises, created_at) VALUES "
                    "('00000000-0000-0000-0000-000000000001', DATE '2026-09-19', "
                    "'maintenance', ?, TIMESTAMPTZ '2026-09-19 10:00:00+00')", [seed])
        con.execute("INSERT INTO user_profiles (id, updated_at, payload) VALUES "
                    "('00000000-0000-0000-0000-000000000002', TIMESTAMPTZ '2026-09-19 10:00:00+00', ?)",
                    [json.dumps({"priority_muscles": ["side_delt"], "notes": "chest day"})])
        con.close()
        dbs[mode] = db
    command.upgrade(_cfg(dbs["online"]), "head")
    assert _run_script(dbs["offline"], _render(
        tmp_path, "upgrade", "0002_profile_memory:0003_vocab_exercise_fields")) == []
    assert _dump(dbs["offline"]) == _dump(dbs["online"])


# --- hand-edited profile payloads of unexpected shape ---------------------------

@pytest.mark.parametrize("payload", [
    {"goals": 5}, {"goals": True}, {"goals": 1.5}, {"goals": "x"}, {"goals": None},
    {"goals": [5, "x", None, {"target_muscles": "upper_chest"}]},
    {"priority_muscles": "upper_chest"}, [1, 2], "text", 7, None,
])
def test_odd_profile_shapes_never_crash_the_upgrade(tmp_path, payload):
    db = tmp_path / "shapes.duckdb"
    command.upgrade(_cfg(db), "0002_profile_memory")
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO user_profiles (payload) VALUES (?)", [json.dumps(payload)])
    con.close()
    command.upgrade(_cfg(db), "head")
    con = duckdb.connect(str(db))
    stored = json.loads(con.execute("SELECT payload FROM user_profiles").fetchone()[0])
    con.close()
    assert stored == payload  # nothing muscle-typed to remap: left as is


@pytest.mark.parametrize("muscles", [[{"m": "chest"}], [["chest"]], [5], [None]])
def test_odd_profile_shapes_are_refused_not_crashed_on_downgrade(legacy_db, muscles):
    db, _ = legacy_db
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO user_profiles (payload) VALUES (?)",
                [json.dumps({"priority_muscles": muscles, "goals": 5})])
    con.close()
    with pytest.raises(RuntimeError, match="vocabulary the old code rejects"):
        command.downgrade(_cfg(db), "0002_profile_memory")
    assert _current_revision(db) == "0003_vocab_exercise_fields"
