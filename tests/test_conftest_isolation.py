"""P31: each pytest run owns a private DB location, so concurrent runs can't collide."""

import os
import tempfile
from pathlib import Path

import conftest


def test_db_lives_in_a_per_run_directory_not_the_shared_temp_root():
    duck = Path(os.environ["GYM_COACH_DUCKDB"])
    assert duck.parent == conftest._RUN_DIR
    assert duck.parent != Path(tempfile.gettempdir())  # not the old shared fixed path
    assert duck.parent.name.startswith("gym_coach_test_")
    assert duck.exists()


def test_never_the_production_db():
    prod = Path(conftest._REPO_ROOT) / "data"
    assert prod not in Path(os.environ["GYM_COACH_DUCKDB"]).parents
