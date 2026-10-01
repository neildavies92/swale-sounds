from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from swale_sounds.config import DatabaseConfig
from swale_sounds.database import (
    create_database_engine,
    create_session_factory,
)
from swale_sounds.models import Session, SessionStatus


@pytest.fixture
def database(tmp_path):
    url = f"sqlite:///{tmp_path}/nested/test.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_database_engine(DatabaseConfig(url=url))
    try:
        yield engine, create_session_factory(engine), config
    finally:
        engine.dispose()


def new_session(public_id="session-000001", status=SessionStatus.CREATED):
    return Session(
        public_id=public_id,
        title="Quiet water",
        status=status,
        spec_version=1,
        spec_path="session.yaml",
        spec_sha256="a" * 64,
        spec_json={
            "title": "Quiet water",
            "nested": {"tracks": [1, True, None]},
        },
        workspace_path="sessions/session-000001",
    )


def test_migration_creates_expected_schema_without_model_drift(database):
    engine, _, config = database
    assert set(inspect(engine).get_table_names()) == {
        "sessions",
        "assets",
        "alembic_version",
    }
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
    command.check(config)
    command.downgrade(config, "base")
    assert "sessions" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")


@pytest.mark.parametrize("status", list(SessionStatus))
def test_session_round_trip_preserves_status_json_and_utc(database, status):
    engine, factory, _ = database
    before = datetime.now(UTC)
    with factory.begin() as db:
        db.add(new_session(status=status))
    with factory() as db:
        saved = db.scalars(select(Session)).one()
        assert saved.id == 1
        assert saved.public_id == "session-000001"
        assert saved.title == "Quiet water"
        assert saved.spec_json["nested"] == {"tracks": [1, True, None]}
        assert saved.status is status
        assert before <= saved.created_at <= datetime.now(UTC)
        assert saved.updated_at.tzinfo is UTC
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT status FROM sessions"))
            == status.value
        )


def test_duplicate_public_id_rolls_back_transaction(database):
    _, factory, _ = database
    with factory.begin() as db:
        db.add(new_session())
    with pytest.raises(IntegrityError), factory.begin() as db:
        db.add(new_session("another"))
        db.add(new_session())
    with factory() as db:
        assert len(db.scalars(select(Session)).all()) == 1


def test_updates_refresh_updated_timestamp(database):
    _, factory, _ = database
    old = datetime.now(UTC) - timedelta(days=1)
    with factory.begin() as db:
        row = new_session()
        row.created_at = old
        row.updated_at = old
        db.add(row)
    with factory.begin() as db:
        db.scalars(select(Session)).one().title = "New title"
    with factory() as db:
        saved = db.scalars(select(Session)).one()
        assert saved.created_at == old
        assert saved.updated_at > old


def test_naive_timestamps_are_rejected(database):
    _, factory, _ = database
    with (
        pytest.raises(StatementError, match="timezone-aware"),
        factory.begin() as db,
    ):
        row = new_session()
        row.created_at = datetime(2020, 1, 1)
        db.add(row)
