"""P78 — very common exercise names resolve to a catalog identity.

The safety gate fails closed on unrecognized names while any ban is active, so
a missing common name was refused outright, left out of safe_alternatives, and
logged as `unclassified` (credited to no muscle).
"""

from __future__ import annotations

import pytest

from models import MuscleGroup
from models.doctrine_ch03 import EXERCISE_PATTERN, HEURISTIC_CLASSIFICATIONS, MovementPattern
from models.exercise_catalog import (
    PRIMARY,
    SECONDARY_OVERLAP,
    canonicalize,
    default_load_type,
    resolve_name,
)
from models import LoadType

M = MuscleGroup

_PUSH = {M.chest, M.front_delt, M.triceps}          # flat horizontal push: no middle delts
_PULL_V = {M.lats, M.biceps, M.rear_delt}
_PULL_H = {M.lats, M.mid_back, M.rear_delt, M.biceps, M.side_delt}
_SQUAT = {M.quads, M.glutes, M.erectors}
_HINGE = {M.glutes, M.hamstrings, M.erectors, M.mid_back}


def _credits(name):
    return {PRIMARY[name], *SECONDARY_OVERLAP[name]}


@pytest.mark.parametrize("raw, identity, muscles, primary, load", [
    ("Push-Up", "Push-Up", _PUSH, M.chest, LoadType.bodyweight),
    ("Push Up", "Push-Up", _PUSH, M.chest, LoadType.bodyweight),
    ("Pushup", "Push-Up", _PUSH, M.chest, LoadType.bodyweight),
    ("push-ups", "Push-Up", _PUSH, M.chest, LoadType.bodyweight),
    ("Chin-Up", "Chin-Up", _PULL_V, M.lats, LoadType.bodyweight),
    ("Chin Up", "Chin-Up", _PULL_V, M.lats, LoadType.bodyweight),
    ("Chinup", "Chin-Up", _PULL_V, M.lats, LoadType.bodyweight),
    ("Chin-Ups", "Chin-Up", _PULL_V, M.lats, LoadType.bodyweight),
    ("Plank", "Plank", {M.core}, M.core, LoadType.bodyweight),
    ("Back Squat", "Back Squat", _SQUAT, M.quads, LoadType.total),
    ("Front Squat", "Front Squat", _SQUAT, M.quads, LoadType.total),
    ("Deadlift", "Deadlift", _HINGE, M.glutes, None),
    ("Conventional Deadlift", "Conventional Deadlift", _HINGE, M.glutes, LoadType.total),
    ("Good Morning", "Good Morning", _HINGE, M.glutes, LoadType.total),
    ("Bent-Over Row", "Barbell Row", _PULL_H, M.lats, LoadType.total),
    ("DB Row", "Dumbbell Row", _PULL_H, M.lats, LoadType.per_hand),
])
def test_common_names_resolve_and_credit_chart_muscles(raw, identity, muscles, primary, load):
    assert resolve_name(raw) == identity
    name, mg, review = canonicalize(raw)
    assert (name, mg, review) == (identity, primary, False)
    assert _credits(identity) == muscles
    assert default_load_type(raw) == load


def test_push_up_is_the_flat_horizontal_push_row_and_chin_up_the_vertical_pull_row():
    assert EXERCISE_PATTERN["Push-Up"] is MovementPattern.horizontal_push
    assert EXERCISE_PATTERN["Chin-Up"] is MovementPattern.vertical_pull
    # same credit as Pull-Up, and a flat press like Bench Press withholds middle delts
    assert _credits("Chin-Up") == _credits("Pull-Up")
    assert _credits("Push-Up") == _credits("Barbell Bench Press")
    # the decline variant keeps the incline middle-delt credit (HEURISTIC)
    assert M.side_delt in _credits("Decline Push-Up")


def test_plank_is_labelled_heuristic_core_isolation():
    assert EXERCISE_PATTERN["Plank"] is MovementPattern.isolation
    assert HEURISTIC_CLASSIFICATIONS["Plank"].startswith("HEURISTIC:")


def _ban(*names, alternatives=("Lateral Raise",)):
    from skills.injuries import seed_injury
    seed_injury("left_shoulder", "active", 5, contraindicated_exercises=list(names),
                safe_alternatives=list(alternatives))


def _check(name):
    from skills.safety_gate import check_exercise_safety
    return check_exercise_safety(name)


@pytest.mark.parametrize("query", ["Push-Up", "push up", "Pushup"])
def test_ban_on_bench_press_also_blocks_push_up(query):
    _ban("Bench Press")
    assert not _check(query).safe


def test_ban_on_push_up_blocks_push_up_but_not_a_sibling():
    _ban("Push Up")
    assert not _check("Pushup").safe
    assert _check("Chin-Up").safe


@pytest.mark.parametrize("ban, query", [
    ("Squat", "Back Squat"), ("squat", "Front Squat"),
    ("Deadlift", "Romanian Deadlift"), ("Deadlift", "Conventional Deadlift"),
    ("Row", "Barbell Row"), ("Row", "Dumbbell Row"),
])
def test_generic_bans_cover_the_new_variants(ban, query):
    _ban(ban)
    assert not _check(query).safe


def test_common_names_are_not_refused_as_unrecognized_while_a_ban_is_active():
    _ban("Barbell Bench Press")
    for q in ("Push-Up", "Chin-Up", "Plank", "Back Squat"):
        r = _check(q)
        assert r.safe, q
        assert "not recognized" not in r.reason


def test_push_up_is_offered_as_a_safe_alternative_when_not_banned():
    _ban("Barbell Bench Press", alternatives=("Push-Up", "Plank", "Lateral Raise"))
    r = _check("Barbell Bench Press")
    assert not r.safe
    assert r.alternatives == ["Push-Up", "Plank", "Lateral Raise"]


def test_push_up_is_not_offered_when_another_row_bans_it():
    from skills.injuries import seed_injury
    _ban("Barbell Bench Press", alternatives=("Push-Up", "Lateral Raise"))
    seed_injury("left_elbow", "active", 4, contraindicated_exercises=["Pushup"],
                safe_alternatives=["Lateral Raise"])
    assert _check("Barbell Bench Press").alternatives == ["Lateral Raise"]
