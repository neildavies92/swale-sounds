from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from swale_sounds.database import create_session_factory
from swale_sounds.models import RenderRun, RenderStage, RenderStatus
from swale_sounds.rendering.service import render_audio
from swale_sounds.rendering.video_service import render_video


def migration_config(settings):
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database.url)
    return config


def record(session_id, public_id, stage, **extra):
    return RenderRun(
        session_id=session_id,
        public_id=public_id,
        stage=stage,
        status=RenderStatus.RUNNING,
        renderer_version=1,
        input_fingerprint="fingerprint",
        ffmpeg_version="ffmpeg test",
        inputs_json={},
        configuration_json={},
        log_path=f"{public_id}.log",
        **extra,
    )


def test_video_metadata_and_stage_scoped_running_uniqueness(asset_session):
    _, engine, _, session = asset_session
    with create_session_factory(engine).begin() as db:
        db.add(record(session.id, "audio", RenderStage.AUDIO))
        db.add(
            record(
                session.id,
                "video",
                RenderStage.VIDEO,
                width=160,
                height=90,
                frame_rate=29.97,
            )
        )
    with create_session_factory(engine)() as db:
        rows = list(db.query(RenderRun).order_by(RenderRun.id))
        assert rows[0].width is None and rows[0].frame_rate is None
        assert (rows[1].width, rows[1].height, rows[1].frame_rate) == (
            160,
            90,
            29.97,
        )
        assert rows[1].started_at.utcoffset().total_seconds() == 0
        assert rows[1].session.id == session.id
    with (
        pytest.raises(IntegrityError),
        create_session_factory(engine).begin() as db,
    ):
        db.add(record(session.id, "second-video", RenderStage.VIDEO))


@pytest.mark.parametrize(
    "field,value",
    [
        ("stage", "invalid"),
        ("width", 0),
        ("height", -1),
        ("frame_rate", 0),
        ("session_id", 99999),
    ],
)
def test_video_database_constraints(asset_session, field, value):
    _, engine, _, session = asset_session
    with create_session_factory(engine).begin() as db:
        db.add(record(session.id, "video", RenderStage.VIDEO))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        # Field comes exclusively from the test's fixed parameter list.
        connection.execute(
            text(f"UPDATE render_runs SET {field} = :value"), {"value": value}
        )


def test_migration_preserves_real_audio_and_allows_video(render_session):
    settings, engine, _, session = render_session
    audio = render_audio(engine, settings, session.public_id).run
    config = migration_config(settings)
    command.downgrade(config, "0003")
    assert "width" not in {
        column["name"] for column in inspect(engine).get_columns("render_runs")
    }
    with engine.connect() as connection:
        before = dict(
            connection.execute(text("SELECT * FROM render_runs"))
            .mappings()
            .one()
        )
        assert before["input_fingerprint"] == audio.input_fingerprint
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        after = dict(
            connection.execute(text("SELECT * FROM render_runs"))
            .mappings()
            .one()
        )
        assert {key: after[key] for key in before} == before
        assert after["width"] is None and after["frame_rate"] is None
    assert render_audio(engine, settings, session.public_id).run.id == audio.id
    settings.media.video.width = 160
    settings.media.video.height = 90
    settings.media.video.fps = 10
    video = render_video(engine, settings, session.public_id).run
    assert video.inputs_json["audio_render"]["render_id"] == audio.public_id
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        command.downgrade(config, "0003")
    with create_session_factory(engine)() as db:
        assert db.get(RenderRun, video.id).status == RenderStatus.SUCCEEDED
