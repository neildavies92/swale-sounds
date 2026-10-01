from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from swale_sounds.config import DatabaseConfig
from swale_sounds.database import (
    create_database_engine,
    create_session_factory,
)
from swale_sounds.models import Asset, AssetKind
from swale_sounds.sessions.service import create_session


def asset_record(session_id, **overrides):
    values = {
        "public_id": "asset-test",
        "session_id": session_id,
        "kind": AssetKind.MUSIC,
        "path": "sessions/session-000001/music/source/test.wav",
        "original_filename": "test.wav",
        "sha256": "a" * 64,
        "size_bytes": 100,
        "provider": "manual",
        "generation_parameters": {"seed": 1},
    }
    values.update(overrides)
    return Asset(**values)


@pytest.mark.parametrize(
    "constraint", ["public_id", "content", "path", "foreign_key"]
)
def test_asset_database_constraints_are_enforced(asset_session, constraint):
    _, engine, _, session = asset_session
    factory = create_session_factory(engine)
    with factory.begin() as db:
        db.add(asset_record(session.id))
    overrides = {
        "public_id": "asset-other",
        "path": "different.wav",
        "sha256": "b" * 64,
    }
    if constraint == "public_id":
        overrides["public_id"] = "asset-test"
    elif constraint == "content":
        overrides["sha256"] = "a" * 64
    elif constraint == "path":
        overrides["path"] = "sessions/session-000001/music/source/test.wav"
    else:
        overrides["session_id"] = 99999
    with pytest.raises(IntegrityError), factory.begin() as db:
        db.add(
            asset_record(overrides.pop("session_id", session.id), **overrides)
        )
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1


def test_session_with_assets_cannot_be_deleted(asset_session):
    _, engine, _, session = asset_session
    with create_session_factory(engine).begin() as db:
        db.add(asset_record(session.id))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("DELETE FROM sessions WHERE id = :id"), {"id": session.id}
        )


def test_upgrade_from_milestone2_preserves_session_and_downgrades(
    tmp_path, spec_file
):
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    url = f"sqlite:///{tmp_path}/upgrade.db"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0001")
    engine = create_database_engine(DatabaseConfig(url=url))
    try:
        session = create_session(engine, tmp_path / "data", spec_file)
        command.upgrade(config, "head")
        assert "assets" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT public_id FROM sessions"))
                == session.public_id
            )
        command.check(config)
        command.downgrade(config, "0001")
        assert "assets" not in inspect(engine).get_table_names()
        assert "sessions" in inspect(engine).get_table_names()
        command.upgrade(config, "head")
    finally:
        engine.dispose()
