"""muscle vocabulary to VARCHAR + per-exercise provenance/load fields + session kind.

0002's vocabulary pivot (ENUM -> VARCHAR) skipped `sessions.exercises
.muscle_group`: it is nested in a STRUCT(...)[] and DuckDB cannot ALTER a type
inside a struct, so the column kept the 0001 `muscle_group` ENUM and any new
vocabulary value failed at insert ("Could not convert string 'chest' to UINT8").

This migration rebuilds `sessions` ONCE and adds every field the vocabulary /
exercise-identity / units / habit work needs, so the struct is not rebuilt
again. It copies into a freshly created table rather than UPDATE-ing a new
column in place (the 0002 pattern): DuckDB rewrites an updated LIST/STRUCT
value as delete + insert, which trips the sessions PRIMARY KEY on commit
("duplicate key") inside one transaction. Ids, dates, created_at and every
other column are copied verbatim.

Per-exercise struct changes:
    muscle_group    ENUM -> VARCHAR (models/enums.MuscleGroup is the only gate);
                    stored 'upper_chest' is remapped to 'chest' (Training ch03's
                    counting chart has one "Chest"; incline vs flat is carried by
                    the exercise name)
    weight_kg       FLOAT[] -> DOUBLE[] so a converted entry (lb * 0.45359237)
                    is stored exactly; legacy float32 values carry over via their
                    shortest decimal text (11.34 stays 11.34, not
                    11.340000152587891)
    raw_name        what the caller originally typed
    muscle_source   how muscle_group was decided: catalog | keyword | caller |
                    unclassified
    load_type       per_hand | per_side | total | machine_stack | bodyweight
    entered_weight  the per-set weights exactly as entered
    entered_unit    'kg' | 'lb'
New fields are NULL on legacy rows, meaning "unknown, pre-0003".

New column sessions.kind ('training' | 'habit', NOT NULL DEFAULT 'training');
legacy rows are training sessions.

user_profiles payloads (append-only JSON history) get the same upper_chest ->
chest remap, applied ONLY to the muscle-typed paths (`priority_muscles`,
`goals[*].target_muscles`) so free-text fields are never rewritten. A rendered
(--sql) script cannot do that per-row work, so it refuses instead whenever a
profile mentions "upper_chest" at all.
decision_log and memory_notes are free-text history, deliberately untouched.

The now-unused `muscle_group` DB enum type is dropped.

Downgrade refuses — before changing anything — whenever stored data has no
faithful representation in the pre-0003 schema (new vocabulary, non-training
sessions, any populated provenance/load field, new vocabulary in a profile).
It never silently drops or rewrites data.

Revision ID: 0003_vocab_exercise_fields
Revises: 0002_profile_memory
Create Date: 2026-10-04
"""

import json

from alembic import context, op

revision = "0003_vocab_exercise_fields"
down_revision = "0002_profile_memory"
branch_labels = None
depends_on = None

_STRUCT_V3 = """STRUCT(
    name VARCHAR,
    muscle_group VARCHAR,
    sets INT,
    reps FLOAT[],
    rpe FLOAT[],
    weight_kg DOUBLE[],
    tempo VARCHAR,
    form_quality INT,
    pain_flag BOOLEAN,
    notes TEXT,
    raw_name VARCHAR,
    muscle_source VARCHAR,
    load_type VARCHAR,
    entered_weight DOUBLE[],
    entered_unit VARCHAR
)[]"""

# The 0001/0002 shape, for downgrade (needs the muscle_group type to exist).
_STRUCT_V1 = """STRUCT(
    name VARCHAR,
    muscle_group muscle_group,
    sets INT,
    reps FLOAT[],
    rpe FLOAT[],
    weight_kg FLOAT[],
    tempo VARCHAR,
    form_quality INT,
    pain_flag BOOLEAN,
    notes TEXT
)[]"""

_OLD_ENUM_VALUES = (
    "side_delt", "rear_delt", "upper_chest", "mid_back", "lats", "biceps",
    "triceps", "quads", "hamstrings", "glutes", "core", "calves", "serratus",
)
_NEW_FIELDS = ("raw_name", "muscle_source", "load_type", "entered_weight", "entered_unit")


def _create_sessions(table: str, exercises_type: str, *, with_kind: bool) -> None:
    """The sessions DDL from 0001/0002 (constraints included), parameterized."""
    kind = ",\n            kind VARCHAR NOT NULL DEFAULT 'training'" if with_kind else ""
    op.execute(
        f"""
        CREATE TABLE {table} (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            date DATE NOT NULL,
            phase VARCHAR,
            pre_recovery_score INT CHECK (pre_recovery_score BETWEEN 0 AND 100),
            exercises {exercises_type},
            post_feedback TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(){kind}
        )
        """
    )


# --- profile payloads: muscle-typed paths only --------------------------------

def _muscle_lists(payload: object) -> list[list]:
    """The JSON lists that hold MuscleGroup values (models/profile.py)."""
    if not isinstance(payload, dict):
        return []
    lists = []
    if isinstance(payload.get("priority_muscles"), list):
        lists.append(payload["priority_muscles"])
    goals = payload.get("goals")
    for goal in goals if isinstance(goals, list) else []:
        if isinstance(goal, dict) and isinstance(goal.get("target_muscles"), list):
            lists.append(goal["target_muscles"])
    return lists


def _profile_rows() -> list[tuple[object, object]]:
    rows = op.get_bind().exec_driver_sql(
        "SELECT id, CAST(payload AS VARCHAR) FROM user_profiles"
    ).fetchall()
    return [(pid, json.loads(raw)) for pid, raw in rows if raw is not None]


def _remap_profile_muscles(old: str, new: str) -> None:
    bind = op.get_bind()
    for pid, payload in _profile_rows():
        changed = False
        for muscles in _muscle_lists(payload):
            for i, m in enumerate(muscles):
                if m == old:
                    muscles[i] = new
                    changed = True
        if changed:
            bind.exec_driver_sql(
                "UPDATE user_profiles SET payload = CAST(? AS JSON) WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), pid),
            )


# --- upgrade ---------------------------------------------------------------------

def upgrade() -> None:
    if context.is_offline_mode():  # checked first: refuse before any change
        # A rendered script cannot run the per-row, path-limited remap, and a
        # text replace would also rewrite free text. So the script refuses
        # (rolling back the whole step) if any profile could need the remap;
        # such a database must be migrated online.
        op.execute(
            """
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM user_profiles
                WHERE CAST(payload AS VARCHAR) LIKE '%"upper_chest"%')
            THEN error('refusing to render 0003 offline for this database: user_profiles '
                       'mention "upper_chest", which only the online upgrade can remap '
                       'safely (muscle-typed paths only) — run alembic upgrade online')
            END
            """
        )
    _create_sessions("sessions_v3", _STRUCT_V3, with_kind=True)
    op.execute(
        f"""
        INSERT INTO sessions_v3
            (id, date, phase, pre_recovery_score, exercises, post_feedback, created_at, kind)
        SELECT id, date, phase, pre_recovery_score,
               CAST(list_transform(exercises, e -> struct_pack(
                   name := e.name,
                   muscle_group := CASE CAST(e.muscle_group AS VARCHAR)
                                       WHEN 'upper_chest' THEN 'chest'
                                       ELSE CAST(e.muscle_group AS VARCHAR) END,
                   sets := e.sets,
                   reps := e.reps,
                   rpe := e.rpe,
                   weight_kg := list_transform(e.weight_kg,
                                               w -> CAST(CAST(w AS VARCHAR) AS DOUBLE)),
                   tempo := e.tempo,
                   form_quality := e.form_quality,
                   pain_flag := e.pain_flag,
                   notes := e.notes,
                   raw_name := CAST(NULL AS VARCHAR),
                   muscle_source := CAST(NULL AS VARCHAR),
                   load_type := CAST(NULL AS VARCHAR),
                   entered_weight := CAST(NULL AS DOUBLE[]),
                   entered_unit := CAST(NULL AS VARCHAR)
               )) AS {_STRUCT_V3}),
               post_feedback, created_at, 'training'
        FROM sessions
        """
    )
    op.execute("DROP TABLE sessions")
    op.execute("ALTER TABLE sessions_v3 RENAME TO sessions")
    op.execute("DROP TYPE muscle_group")

    if not context.is_offline_mode():
        _remap_profile_muscles("upper_chest", "chest")


# --- downgrade -------------------------------------------------------------------

def _refusal(problems: list[str]) -> str:
    return (
        "refusing to downgrade 0003 — stored data cannot be represented in the "
        "pre-0003 schema without loss or corruption:\n  - " + "\n  - ".join(problems)
        + "\nAmend or delete those rows first, or restore a pre-0003 backup."
    )


def _old_enum_sql_list() -> str:
    # 'chest' is representable: it maps back to 'upper_chest'.
    return ", ".join(f"'{v}'" for v in (*_OLD_ENUM_VALUES, "chest"))


def _refuse_if_unrepresentable() -> None:
    """Inspect the data and refuse, naming every problem, before any change."""
    bind = op.get_bind()
    problems = []

    bad_muscles = bind.exec_driver_sql(
        f"""
        SELECT m, count(*) FROM (SELECT UNNEST(exercises).muscle_group AS m FROM sessions)
        WHERE m IS NOT NULL AND m NOT IN ({_old_enum_sql_list()})
        GROUP BY m ORDER BY m
        """
    ).fetchall()
    if bad_muscles:
        problems.append("sessions.exercises.muscle_group values the old enum cannot hold: "
                        + ", ".join(f"{m} ({n})" for m, n in bad_muscles))

    populated = " OR ".join(f"e.{f} IS NOT NULL" for f in _NEW_FIELDS)
    n_new = bind.exec_driver_sql(
        f"SELECT count(*) FROM (SELECT UNNEST(exercises) AS e FROM sessions) WHERE {populated}"
    ).fetchone()[0]
    if n_new:
        problems.append(f"{n_new} exercise(s) carry provenance/load fields the old struct "
                        f"has no place for ({', '.join(_NEW_FIELDS)})")

    non_training = bind.exec_driver_sql(
        "SELECT count(*) FROM sessions WHERE kind <> 'training'"
    ).fetchone()[0]
    if non_training:
        problems.append(f"{non_training} non-training session(s) — the old schema has no "
                        "kind column and would silently turn them into training sessions")

    representable = {*_OLD_ENUM_VALUES, "chest"}
    bad_profile = sorted({
        repr(m) for _pid, payload in _profile_rows()
        for muscles in _muscle_lists(payload) for m in muscles
        if not isinstance(m, str) or m not in representable
    })
    if bad_profile:
        problems.append("user_profiles payloads use vocabulary the old code rejects: "
                        + ", ".join(bad_profile))

    if problems:
        raise RuntimeError(_refusal(problems))


def _emit_offline_guards() -> None:
    """Offline (--sql) mode cannot inspect data now, so the rendered script
    carries the same refusals as guard statements that abort it when run."""
    populated = " OR ".join(f"e.{f} IS NOT NULL" for f in _NEW_FIELDS)
    guards = [
        (f"""EXISTS (SELECT 1 FROM (SELECT UNNEST(exercises).muscle_group AS m FROM sessions)
                     WHERE m IS NOT NULL AND m NOT IN ({_old_enum_sql_list()}))""",
         "muscle_group values the old enum cannot hold"),
        (f"EXISTS (SELECT 1 FROM (SELECT UNNEST(exercises) AS e FROM sessions) WHERE {populated})",
         "exercises carry provenance/load fields the old struct has no place for"),
        ("EXISTS (SELECT 1 FROM sessions WHERE kind <> 'training')",
         "non-training sessions would silently become training sessions"),
        # Without per-row Python the profile paths cannot be told apart from
        # free text, so ANY "chest" token refuses rather than risking a rewrite.
        ("""EXISTS (SELECT 1 FROM user_profiles
                    WHERE CAST(payload AS VARCHAR) LIKE '%"front_delt"%'
                       OR CAST(payload AS VARCHAR) LIKE '%"erectors"%'
                       OR CAST(payload AS VARCHAR) LIKE '%"unclassified"%'
                       OR CAST(payload AS VARCHAR) LIKE '%"chest"%')""",
         "user_profiles hold vocabulary that cannot be remapped safely offline"),
    ]
    for condition, reason in guards:
        op.execute(
            f"SELECT CASE WHEN {condition} THEN "
            f"error('refusing to downgrade 0003: {reason}') END"
        )


def downgrade() -> None:
    if context.is_offline_mode():
        _emit_offline_guards()
    else:
        _refuse_if_unrepresentable()

    old = ", ".join(f"'{v}'" for v in _OLD_ENUM_VALUES)
    op.execute(f"CREATE TYPE muscle_group AS ENUM ({old})")
    _create_sessions("sessions_v2", _STRUCT_V1, with_kind=False)
    op.execute(
        f"""
        INSERT INTO sessions_v2
            (id, date, phase, pre_recovery_score, exercises, post_feedback, created_at)
        SELECT id, date, phase, pre_recovery_score,
               CAST(list_transform(exercises, e -> struct_pack(
                   name := e.name,
                   muscle_group := CASE e.muscle_group WHEN 'chest' THEN 'upper_chest'
                                                       ELSE e.muscle_group END,
                   sets := e.sets,
                   reps := e.reps,
                   rpe := e.rpe,
                   weight_kg := CAST(e.weight_kg AS FLOAT[]),
                   tempo := e.tempo,
                   form_quality := e.form_quality,
                   pain_flag := e.pain_flag,
                   notes := e.notes
               )) AS {_STRUCT_V1}),
               post_feedback, created_at
        FROM sessions
        """
    )
    op.execute("DROP TABLE sessions")
    op.execute("ALTER TABLE sessions_v2 RENAME TO sessions")
    if not context.is_offline_mode():  # offline: the guard above refused any "chest"
        _remap_profile_muscles("chest", "upper_chest")
