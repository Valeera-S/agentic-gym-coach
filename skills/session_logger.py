"""session_logger — validate + persist one gym session to DuckDB.

Flow:
  1. Pydantic already validated `data` on construction (caller's job).
  2. Resolve `phase` if the user didn't set it — skills.phase.phase_for_logging
     (current snapshot → modal training-session phase → maintenance default) so the
     user never has to tag a phase.
  3. Canonicalize each exercise: fill muscle_group from the catalog and
     store the canonical identity name, plus `raw_name` — what the caller
     actually typed.
  4. Record muscle_source (catalog | keyword | caller | unclassified) and
     raise anomaly flags: pain_flag, form_quality<3, and needs_review for a
     name the catalog does not know — worded by source, so a caller-set
     muscle is never reported as a guess.
  5. INSERT into sessions, RETURNING the generated id.

Contract: <50ms. Never fabricates fields the user didn't provide.
"""

from __future__ import annotations

from datetime import date

from models import (
    AnomalyCode,
    AnomalyFlag,
    ExerciseModel,
    LogConfirmation,
    SessionInput,
    MuscleGroup,
    MuscleSource,
    classify,
)

from models.exercise_catalog import default_load_type, resolve_name

from .init import get_duckdb
from .phase import phase_for_logging


def _canonicalize_exercises(data: SessionInput) -> list[MuscleSource]:
    """Canonicalize names, fill muscle_group, return each exercise's
    muscle_source. Mutates `data.exercises` in place."""
    sources: list[MuscleSource] = []
    for ex in data.exercises:
        c = classify(ex.name)
        ex.name = c.name
        if ex.muscle_group is None:
            ex.muscle_group = c.muscle_group
            sources.append(c.source)
        else:
            # caller-supplied muscle wins over the catalog / a guess
            sources.append(MuscleSource.caller)
        if ex.load_type is None:
            # the identity's default; a caller-supplied load_type always wins
            ex.load_type = default_load_type(c.name)
    return sources


def _anomaly_flags(data: SessionInput, sources: list[MuscleSource],
                   in_catalog: list[bool]) -> list[AnomalyFlag]:
    flags: list[AnomalyFlag] = []
    for ex, source, known in zip(data.exercises, sources, in_catalog, strict=True):
        if not known:
            flags.append(AnomalyFlag(code=AnomalyCode.needs_review,
                                     detail=review_detail(ex.name, ex.muscle_group, source)))
        if ex.pain_flag:
            flags.append(AnomalyFlag(
                code=AnomalyCode.pain_flag, detail=f"{ex.name}: pain during exercise"))
        if ex.form_quality < 3:
            flags.append(AnomalyFlag(
                code=AnomalyCode.form_quality_low,
                detail=f"{ex.name}: form_quality={ex.form_quality} (volume -50% downstream)",
            ))
    return flags


def review_detail(name: str, muscle: MuscleGroup | None, source: MuscleSource) -> str:
    """The needs_review message for a name the catalog does not know, stating
    truthfully where its muscle came from."""
    mg = muscle.value if muscle else "none"
    if source is MuscleSource.caller:
        if muscle is MuscleGroup.unclassified:
            return (f"exercise '{name}' is not in the catalog; caller set it unclassified: "
                    "logged, but its sets credit no muscle until it is mapped")
        return f"exercise '{name}' is not in the catalog; muscle {mg} set by caller"
    if source is MuscleSource.keyword:
        return f"unmapped exercise '{name}' guessed by keyword as {mg}"
    return (f"unmapped exercise '{name}' is unclassified: logged, but its sets "
            "credit no muscle until it is mapped")


def _to_struct_list(exercises: list[ExerciseModel], raw_names: list[str],
                    sources: list[MuscleSource]) -> list[dict]:
    return [
        {
            "name": ex.name,
            "raw_name": raw,
            "muscle_source": source.value,
            "muscle_group": ex.muscle_group.value if ex.muscle_group else None,
            "sets": ex.sets,
            "reps": ex.reps,
            "rpe": ex.rpe,
            "weight_kg": ex.weight_kg,
            "tempo": ex.tempo,
            "form_quality": ex.form_quality,
            "pain_flag": ex.pain_flag,
            "notes": ex.notes,
            "load_type": ex.load_type.value if ex.load_type else None,
            # entered values are kept only when the caller used weight + unit;
            # a weight_kg caller entered kg, which weight_kg already holds
            "entered_weight": ex.weight if ex.unit else None,
            "entered_unit": ex.unit.value if ex.unit else None,
        }
        for ex, raw, source in zip(exercises, raw_names, sources, strict=True)
    ]


def log_session(data: SessionInput) -> LogConfirmation:
    phase = phase_for_logging(data.phase)
    # What the caller typed, kept beside the canonical identity (read-back,
    # and the record of what a canonicalization decision was made from).
    raw_names = [ex.name for ex in data.exercises]
    sources = _canonicalize_exercises(data)
    in_catalog = [resolve_name(ex.name) is not None for ex in data.exercises]
    flags = _anomaly_flags(data, sources, in_catalog)

    structs = _to_struct_list(data.exercises, raw_names, sources)
    row = get_duckdb().execute(
        """
        INSERT INTO sessions (date, phase, pre_recovery_score, exercises, post_feedback, kind)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id, date
        """,
        [data.date, phase.value, data.pre_recovery_score, structs, data.post_feedback,
         data.kind.value],
    ).fetchone()

    session_id, session_date = row[0], row[1]
    return LogConfirmation(
        session_id=session_id,
        date=session_date,
        anomaly_flags=flags,
        message="session logged",
    )