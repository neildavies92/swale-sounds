"""Use application configuration unless a caller supplies a database URL."""

from pathlib import Path

from alembic import context
from sqlalchemy.engine import make_url

from swale_sounds.config import DatabaseConfig, load_config
from swale_sounds.database import Base, create_database_engine
from swale_sounds.models import Session

assert Session.metadata is Base.metadata
config = context.config
target_metadata = Base.metadata


def database_config() -> DatabaseConfig:
    override = config.get_main_option("sqlalchemy.url")
    if override:
        return DatabaseConfig(url=override)
    path = context.get_x_argument(as_dictionary=True).get(
        "config", "config/swale-sounds.yaml"
    )
    return load_config(Path(path)).database


settings = database_config()
if context.is_offline_mode():
    context.configure(
        url=settings.url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    database = make_url(settings.url).database
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(settings)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
