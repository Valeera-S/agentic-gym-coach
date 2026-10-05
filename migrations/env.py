import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy.engine import make_url

from alembic import context
from alembic.ddl.impl import DefaultImpl


# duckdb-engine ships no Alembic impl; register a bare one so op.execute()
# raw-SQL migrations work. We don't use autogenerate, so this is enough.
class AlembicDuckDBImpl(DefaultImpl):
    __dialect__ = "duckdb"

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- target database ---------------------------------------------------------
# skills/init.py lets GYM_COACH_DUCKDB redirect the app to any DB file; the
# alembic CLI must honour the same variable, or `alembic upgrade head` can only
# ever migrate the hard-coded data/gym_coach.duckdb.
#
# Precedence: an explicit URL (`Config.set_main_option`, as conftest.py and
# programmatic callers do) > GYM_COACH_DUCKDB > the alembic.ini default. The env
# var replaces only the ini DEFAULT, never an explicit URL: an ambient variable
# left in a shell must not silently redirect a call that named its target.
# (One unavoidable exception: an explicit URL equal to the ini default is
# indistinguishable from it, so the env var still applies there.)
_ENV_DB = "GYM_COACH_DUCKDB"


def _ini_default_db() -> Path | None:
    """The DB path alembic.ini's default URL resolves to (`%(here)s/data/...`)."""
    if config.config_file_name is None:
        return None
    here = Path(config.config_file_name).resolve().parent
    return here / "data" / "gym_coach.duckdb"


def _db_path(url: str | None) -> Path | None:
    """Filesystem path of a duckdb:/// URL; None for in-memory or non-file URLs."""
    if not url:
        return None
    database = make_url(url).database
    if not database or database == ":memory:" or database.startswith("md:"):
        return None
    return Path(database)


def _same_file(a: Path | None, b: Path | None) -> bool:
    return a is not None and b is not None and a.resolve() == b.resolve()


def _resolve_target_url() -> str | None:
    url = config.get_main_option("sqlalchemy.url")
    env_db = os.environ.get(_ENV_DB)
    if env_db and (not url or _same_file(_db_path(url), _ini_default_db())):
        url = f"duckdb:///{env_db}"
        # set_main_option interpolates: a literal '%' in the path must be doubled
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return url


def _ensure_parent_dir(url: str | None) -> None:
    """duckdb-engine will not create missing directories, so a fresh clone (no
    data/) failed `alembic upgrade head`. skills/init.py already
    mkdirs before connecting; do the same here."""
    path = _db_path(url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)


_resolve_target_url()

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = None

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Wrap the rendered script in BEGIN/COMMIT. A --sql script may be run
        # by a tool that reports an error and carries on (the DuckDB CLI does
        # so without -bail); a migration's guard statement must then abort the
        # whole script, version bump included, not just itself. Online runs
        # are already transactional per migration.
        transactional_ddl=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    _ensure_parent_dir(config.get_main_option("sqlalchemy.url"))
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
