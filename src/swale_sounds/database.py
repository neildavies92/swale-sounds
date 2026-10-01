"""SQLite engines and explicit transaction-scoped ORM sessions."""

from sqlite3 import Connection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from swale_sounds.config import DatabaseConfig


class Base(DeclarativeBase):
    """Shared metadata for application tables and Alembic."""


def create_database_engine(config: DatabaseConfig) -> Engine:
    """Create an engine; provisioning its parent directory is caller-owned."""
    engine = create_engine(config.url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(
        connection: Connection, record: ConnectionPoolEntry
    ) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Use factory.begin() to commit on success or roll back on failure."""
    return sessionmaker(bind=engine)
