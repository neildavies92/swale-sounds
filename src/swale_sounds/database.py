"""SQLite engines and explicit transaction-scoped ORM sessions."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from swale_sounds.config import DatabaseConfig


class Base(DeclarativeBase):
    """Shared metadata for application tables and Alembic."""


def create_database_engine(config: DatabaseConfig) -> Engine:
    """Create an engine; provisioning its parent directory is caller-owned."""
    return create_engine(config.url)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Use factory.begin() to commit on success or roll back on failure."""
    return sessionmaker(bind=engine)
