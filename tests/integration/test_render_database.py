from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from swale_sounds.assets.provenance import Provenance
from swale_sounds.assets.service import import_assets, list_assets
from swale_sounds.database import create_session_factory
from swale_sounds.models import (
    AssetKind,
    RenderRun,
    RenderStage,
    RenderStatus,
    Session,
)
from swale_sounds.rendering.service import record_failure


def run_record(session_id, **overrides):
    values = {
        "public_id": "render-test",
        "session_id": session_id,
        "stage": RenderStage.AUDIO,
        "status": RenderStatus.RUNNING,
        "renderer_version": 1,
        "input_fingerprint": "a" * 64,
        "ffmpeg_version": "ffmpeg test",
        "inputs_json": {"music": [], "ambience": None},
        "configuration_json": {"sample_rate": 48000},
        "log_path": "sessions/session-000001/logs/render-test.log",
    }
    values.update(overrides)
    return RenderRun(**values)


def test_render_persistence_relationship_and_utc(asset_session):
    _, engine, _, session = asset_session
    with create_session_factory(engine).begin() as db:
        db.add(run_record(session.id))
    with create_session_factory(engine)() as db:
        saved_session = db.get(Session, session.id)
        run = saved_session.render_runs[0]
        assert run.session.public_id == session.public_id
        assert run.started_at.tzinfo is UTC
        assert run.inputs_json == {"music": [], "ambience": None}
        assert run.configuration_json == {"sample_rate": 48000}


@pytest.mark.parametrize(
    "constraint",
    [
        "public_id",
        "foreign_key",
        "running",
        "renderer_version",
        "sample_rate",
        "channels",
        "size",
        "duration",
    ],
)
def test_render_constraints_are_enforced(asset_session, constraint):
    _, engine, _, session = asset_session
    with create_session_factory(engine).begin() as db:
        db.add(run_record(session.id))
    overrides = {"public_id": "render-other", "status": RenderStatus.FAILED}
    if constraint == "public_id":
        overrides["public_id"] = "render-test"
    elif constraint == "foreign_key":
        overrides["session_id"] = 9999
    elif constraint == "running":
        overrides["status"] = RenderStatus.RUNNING
    else:
        field = {
            "size": "output_size_bytes",
            "duration": "duration_seconds",
        }.get(constraint, constraint)
        overrides[field] = -1
    with (
        pytest.raises(IntegrityError),
        create_session_factory(engine).begin() as db,
    ):
        db.add(
            run_record(overrides.pop("session_id", session.id), **overrides)
        )


def test_completed_runs_are_not_rewritten_by_failure_handler(asset_session):
    _, engine, _, session = asset_session
    with create_session_factory(engine).begin() as db:
        row = run_record(
            session.id,
            status=RenderStatus.SUCCEEDED,
            finished_at=datetime.now(UTC),
        )
        db.add(row)
        db.flush()
        run_id = row.id
    record_failure(engine, run_id, "late error")
    with create_session_factory(engine)() as db:
        assert db.get(RenderRun, run_id).status == RenderStatus.SUCCEEDED
        assert db.get(RenderRun, run_id).error_message is None


def test_upgrade_from_milestone3_preserves_assets_and_downgrades(
    asset_session, media_files
):
    settings, engine, _, session = asset_session
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database.url)
    command.downgrade(config, "0002")
    assert "render_runs" not in inspect(engine).get_table_names()
    import_assets(
        engine,
        settings.paths.data,
        session.public_id,
        media_files[0],
        AssetKind.MUSIC,
        Provenance(),
    )
    imported = list_assets(engine, session.public_id)[0]
    command.upgrade(config, "head")
    assert "render_runs" in inspect(engine).get_table_names()
    command.check(config)
    with create_session_factory(engine)() as db:
        assert db.get(Session, session.id).public_id == session.public_id
    assert list_assets(engine, session.public_id)[0].sha256 == imported.sha256
