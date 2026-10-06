"""profile edge cases — missing/empty profile, priority derivation, goal-change audit."""

from skills.init import get_duckdb
from skills.profile import derive_priority_muscles, get_profile, set_profile
from models import Goal, GoalKind, MuscleGroup, UserProfile


def test_missing_profile_is_none_and_derives_no_priorities():
    assert get_profile() is None
    assert derive_priority_muscles(None) == []


def test_profile_with_no_goals_or_priorities_derives_empty():
    set_profile(UserProfile())  # all-defaults profile
    p = get_profile()
    assert p is not None
    assert derive_priority_muscles(p) == []


def test_priority_muscles_declared_then_goal_targets_appended():
    set_profile(UserProfile(
        priority_muscles=[MuscleGroup.lats],
        goals=[Goal(kind=GoalKind.hypertrophy,
                    target_muscles=[MuscleGroup.side_delt, MuscleGroup.lats])],
    ))
    p = get_profile()
    assert derive_priority_muscles(p) == [MuscleGroup.lats, MuscleGroup.side_delt]


def test_every_set_profile_appends_a_row():
    set_profile(UserProfile(display_name="a"))
    set_profile(UserProfile(display_name="b"))
    n = get_duckdb().execute("SELECT count(*) FROM user_profiles").fetchone()[0]
    assert n == 2
    assert get_profile().display_name == "b"


def test_goal_change_is_audited_unchanged_is_not():
    set_profile(UserProfile(goals=[Goal(kind=GoalKind.hypertrophy,
                                        target_muscles=[MuscleGroup.lats])]))
    set_profile(UserProfile(goals=[Goal(kind=GoalKind.hypertrophy,
                                        target_muscles=[MuscleGroup.lats])]))  # same goals
    n0 = get_duckdb().execute(
        "SELECT count(*) FROM decision_log WHERE event_type='goal_change'").fetchone()[0]
    assert n0 == 0
    set_profile(UserProfile(goals=[Goal(kind=GoalKind.strength,
                                        target_muscles=[MuscleGroup.lats])]))
    n1 = get_duckdb().execute(
        "SELECT count(*) FROM decision_log WHERE event_type='goal_change'").fetchone()[0]
    assert n1 == 1


def test_empty_goals_to_real_goal_is_audited():
    set_profile(UserProfile())
    set_profile(UserProfile(goals=[Goal(kind=GoalKind.fat_loss)]))
    n = get_duckdb().execute(
        "SELECT count(*) FROM decision_log WHERE event_type='goal_change'").fetchone()[0]
    assert n == 1


# --- P51: the goal audit compares full goal content and describes the change exactly ----

def _audit_rows():
    return [r[0] for r in get_duckdb().execute(
        "SELECT trigger_signal FROM decision_log WHERE event_type='goal_change' "
        "ORDER BY created_at, id").fetchall()]


def _G(**kw):
    kw.setdefault("kind", GoalKind.hypertrophy)
    return Goal(**kw)


def _sets(before, after):
    set_profile(UserProfile(goals=before))
    get_duckdb().execute("DELETE FROM decision_log")  # only the before -> after audit counts
    set_profile(UserProfile(goals=after))
    return _audit_rows()


def test_physique_target_change_is_audited_with_old_and_new():
    from models import PhysiqueTarget
    rows = _sets([_G(kind=GoalKind.fat_loss, physique_target=PhysiqueTarget.ripped)],
                 [_G(kind=GoalKind.fat_loss, physique_target=PhysiqueTarget.athletic)])
    assert len(rows) == 1
    assert "physique_target" in rows[0] and "ripped" in rows[0] and "athletic" in rows[0]
    assert "removed" not in rows[0] and "added" not in rows[0]


def test_deadline_metric_and_notes_changes_are_audited():
    from datetime import date
    for field, old, new in (("deadline", date(2026, 12, 1), date(2027, 1, 1)),
                            ("metric", "first pull-up", "5 pull-ups"),
                            ("notes", "a", "b")):
        get_duckdb().execute("DELETE FROM decision_log")
        rows = _sets([_G(**{field: old})], [_G(**{field: new})])
        assert len(rows) == 1, field
        assert field in rows[0] and str(old) in rows[0] and str(new) in rows[0]


def test_target_muscles_change_names_the_field_not_a_phantom_swap():
    rows = _sets([_G(target_muscles=[MuscleGroup.quads])],
                 [_G(target_muscles=[MuscleGroup.quads, MuscleGroup.glutes])])
    assert len(rows) == 1
    assert "target_muscles" in rows[0] and "glutes" in rows[0]
    assert "removed=['hypertrophy'] added=['hypertrophy']" not in rows[0]


def test_adding_a_duplicate_goal_is_audited_as_an_addition():
    rows = _sets([_G()], [_G(), _G()])
    assert len(rows) == 1
    assert "added" in rows[0] and "hypertrophy" in rows[0]
    assert "added=none" not in rows[0]


def test_removing_a_duplicate_goal_is_audited():
    rows = _sets([_G(), _G()], [_G()])
    assert len(rows) == 1 and "removed" in rows[0]


def test_pure_reordering_is_not_a_change():
    a = _G(kind=GoalKind.strength)
    b = _G(target_muscles=[MuscleGroup.quads, MuscleGroup.glutes])
    assert _sets([a, b], [b, a]) == []
    # target muscles are an unordered set too
    assert _sets([_G(target_muscles=[MuscleGroup.quads, MuscleGroup.glutes])],
                 [_G(target_muscles=[MuscleGroup.glutes, MuscleGroup.quads])]) == []


def test_adding_and_removing_goals_are_described_precisely():
    rows = _sets([_G(kind=GoalKind.strength)], [_G(kind=GoalKind.fat_loss)])
    assert len(rows) == 1
    assert "removed=['strength']" in rows[0] and "added=['fat_loss']" in rows[0]


def test_several_goals_of_one_kind_changing_at_once_does_not_crash():
    from models import PhysiqueTarget
    rows = _sets([_G(), _G(physique_target=PhysiqueTarget.ripped), _G(notes="x")],
                 [_G(metric="m"), _G(physique_target=PhysiqueTarget.bulky), _G(notes="y")])
    assert len(rows) == 1 and "changed" in rows[0]


def test_nothing_changed_writes_nothing():
    assert _sets([_G(notes="x")], [_G(notes="x")]) == []
