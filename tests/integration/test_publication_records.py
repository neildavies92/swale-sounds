from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from typer.testing import CliRunner

from swale_sounds.cli import app
from swale_sounds.database import create_session_factory
from swale_sounds.models import Publication, RenderRun
from swale_sounds.publishing.models import PublicationError, plan_bytes
from swale_sounds.publishing.records import (
    list_publications,
    publication_time,
    record_publication,
)
from swale_sounds.publishing.service import create_publication_plan
from swale_sounds.sessions.schema import sha256_bytes


@pytest.fixture
def planned(publication_session):
    settings, engine, _, session, _, _ = publication_session
    plan, path = create_publication_plan(engine, settings, session.public_id)
    return publication_session, plan, path


def test_record_exact_snapshot_idempotency_and_inspection(planned):
    (settings, engine, config, session, _, video), plan, path = planned
    before = path.read_bytes()
    row, reused = record_publication(
        engine,
        settings,
        session.public_id,
        "LocalTest01",
        "2026-10-06T12:30:00+01:00",
    )
    assert not reused
    assert row.plan_text.encode() == before
    assert row.plan_sha256 == sha256_bytes(before)
    assert row.plan_version == plan.plan_version
    assert row.canonical_url == "https://www.youtube.com/watch?v=LocalTest01"
    second, reused = record_publication(
        engine, settings, session.public_id, "LocalTest01"
    )
    assert reused and second.id == row.id
    rows = list_publications(engine)
    assert len(rows) == 1
    assert rows[0].session.public_id == session.public_id
    assert rows[0].video_render.id == video.id
    assert rows[0].published_at == datetime(2026, 10, 6, 11, 30, tzinfo=UTC)
    assert str(settings.paths.data) not in row.plan_text
    for args in (["list"], ["show", row.public_id]):
        result = CliRunner().invoke(
            app, ["publish", *args, "--config", str(config)]
        )
        assert result.exit_code == 0 and row.plan_sha256 in result.output
    record_publication(engine, settings, session.public_id, "LocalTest02")
    assert len(list_publications(engine)) == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "malformed",
        "edited",
        "stale",
        "video_missing",
        "video_hash",
        "wrong_session",
        "missing_run",
        "unsafe",
        "symlink",
    ],
)
def test_record_rejects_invalid_plan_without_rewriting(planned, damage):
    (settings, engine, _, session, _, video), plan, path = planned
    if damage == "missing":
        path.unlink()
    elif damage == "malformed":
        path.write_text("{bad")
    elif damage == "edited":
        path.write_bytes(
            plan_bytes(plan.model_copy(update={"title": "edited"}))
        )
    elif damage == "stale":
        settings.media.video.fps = 12
    elif damage == "video_missing":
        (settings.paths.data / video.output_path).unlink()
    elif damage == "video_hash":
        (settings.paths.data / video.output_path).write_bytes(b"changed")
    elif damage == "wrong_session":
        path.write_bytes(
            plan_bytes(plan.model_copy(update={"session_id": "other"}))
        )
    elif damage == "missing_run":
        with create_session_factory(engine).begin() as db:
            db.delete(db.get(RenderRun, video.id))
    elif damage == "unsafe":
        path.write_bytes(
            path.read_bytes().replace(plan.video.path.encode(), b"../outside")
        )
    else:
        path.unlink()
        path.symlink_to(settings.paths.data / session.spec_path)
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(PublicationError):
        record_publication(engine, settings, session.public_id, "LocalTest01")
    assert not list_publications(engine)
    assert (path.read_bytes() if path.exists() else None) == before


def test_conflicting_id_preserves_history(planned):
    from swale_sounds.rendering.video_service import render_video

    (settings, engine, _, session, _, _), _, _ = planned
    original, _ = record_publication(
        engine, settings, session.public_id, "LocalTest01"
    )
    render_video(engine, settings, session.public_id, force=True)
    create_publication_plan(engine, settings, session.public_id)
    with pytest.raises(PublicationError, match="conflicting"):
        record_publication(engine, settings, session.public_id, "LocalTest01")
    assert list_publications(engine)[0].plan_text == original.plan_text


def test_migration_preservation_and_history_protection(planned):
    (settings, engine, _, session, _, _), _, _ = planned
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database.url)

    def snapshot():
        with engine.connect() as conn:
            return {
                t: conn.exec_driver_sql(f"SELECT * FROM {t}").all()
                for t in ("sessions", "assets", "render_runs")
            }

    before = snapshot()
    command.downgrade(config, "0004")
    command.upgrade(config, "head")
    command.check(config)
    assert snapshot() == before
    row, _ = record_publication(
        engine, settings, session.public_id, "LocalTest01"
    )
    assert snapshot() == before
    with pytest.raises(RuntimeError, match="preserve"):
        command.downgrade(config, "0004")
    for query in (
        "UPDATE publications SET plan_text = 'changed'",
        "DELETE FROM publications",
    ):
        with (
            pytest.raises(IntegrityError, match="immutable"),
            engine.begin() as conn,
        ):
            conn.exec_driver_sql(query)
    assert list_publications(engine)[0].plan_sha256 == row.plan_sha256


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", 99999),
        ("video_render_id", 99999),
        ("platform", "other"),
        ("status", "bad"),
        ("external_id", ""),
        ("plan_sha256", "Z" * 64),
        ("plan_version", 0),
    ],
)
def test_insert_constraints(planned, field, value):
    (settings, engine, _, session, _, _), _, _ = planned
    original, _ = record_publication(
        engine, settings, session.public_id, "LocalTest01"
    )
    values = {
        c.name: getattr(original, c.name)
        for c in Publication.__table__.columns
        if c.name != "id"
    }
    values.update(public_id="publication-other", external_id="LocalTest02")
    values[field] = value
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(Publication.__table__.insert().values(**values))


def test_duplicate_constraint_and_wrong_render_stage(planned):
    (settings, engine, _, session, audio, _), _, _ = planned
    original, _ = record_publication(
        engine, settings, session.public_id, "LocalTest01"
    )
    values = {
        c.name: getattr(original, c.name)
        for c in Publication.__table__.columns
        if c.name != "id"
    }
    values["public_id"] = "publication-other"
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(Publication.__table__.insert().values(**values))
    values.update(external_id="LocalTest02", video_render_id=audio.id)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(Publication.__table__.insert().values(**values))


def test_cli_record_and_errors(planned):
    (_, _, config, session, _, _), _, _ = planned
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "publish",
            "record",
            session.public_id,
            "--video-id",
            "LocalTest01",
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 0 and "Recorded Publication" in result.output
    result = runner.invoke(
        app,
        [
            "publish",
            "record",
            session.public_id,
            "--video-id",
            "invalid",
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 1
    result = runner.invoke(
        app, ["publish", "show", "missing", "--config", str(config)]
    )
    assert result.exit_code == 1 and "not found" in result.output


def test_time_validation_and_injected_default(monkeypatch):
    now = datetime(2026, 10, 6, tzinfo=UTC)
    monkeypatch.setattr("swale_sounds.publishing.records.utc_now", lambda: now)
    assert publication_time(None) == now
    for value in ("bad", "2026-10-06T12:00:00"):
        with pytest.raises(PublicationError, match="timezone"):
            publication_time(value)
