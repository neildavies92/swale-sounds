import pytest
import yaml
from sqlalchemy import select
from typer.testing import CliRunner

from swale_sounds.assets.files import calculate_sha256
from swale_sounds.assets.provenance import Provenance
from swale_sounds.assets.service import import_assets
from swale_sounds.cli import app
from swale_sounds.database import create_session_factory
from swale_sounds.models import (
    Asset,
    AssetKind,
    RenderRun,
    RenderStage,
    RenderStatus,
    Session,
    SessionStatus,
)
from swale_sounds.rendering.ffmpeg import RenderError
from swale_sounds.rendering.service import render_audio
from swale_sounds.rendering.video_service import render_video


@pytest.fixture
def video_session(render_session):
    settings, engine, config, session = render_session
    settings.media.video.width = 160
    settings.media.video.height = 90
    settings.media.video.fps = 10
    config.write_text(yaml.safe_dump(settings.model_dump(mode="json")))
    audio = render_audio(engine, settings, session.public_id).run
    return settings, engine, config, session, audio


def video_rows(engine):
    with create_session_factory(engine)() as db:
        return list(
            db.scalars(
                select(RenderRun)
                .where(RenderRun.stage == RenderStage.VIDEO)
                .order_by(RenderRun.id)
            )
        )


def test_real_video_reuse_force_and_changed_configuration(
    video_session, monkeypatch
):
    settings, engine, _, session, audio = video_session
    workspace = settings.paths.data / session.workspace_path
    before = {
        p: calculate_sha256(p) for p in workspace.rglob("*") if p.is_file()
    }
    result = render_video(engine, settings, session.public_id)
    run = result.run
    assert not result.reused and run.status == RenderStatus.SUCCEEDED
    assert run.codec_name == "h264" and (run.width, run.height) == (160, 90)
    assert run.frame_rate == pytest.approx(10)
    assert (run.sample_rate, run.channels) == (48000, 2)
    assert run.duration_seconds == pytest.approx(60, abs=0.25)
    assert (
        calculate_sha256(settings.paths.data / run.output_path)
        == run.output_sha256
    )
    assert run.inputs_json["audio_render"]["render_id"] == audio.public_id
    assert run.inputs_json["audio_render"]["sha256"] == audio.output_sha256
    assert run.configuration_json["audio_mode"] == "copy"
    assert all(calculate_sha256(p) == digest for p, digest in before.items())
    with monkeypatch.context() as patch:

        def unexpected(*args, **kwargs):
            pytest.fail("Reuse must not encode")

        patch.setattr(
            "swale_sounds.rendering.video_service.render_video_pipeline",
            unexpected,
        )
        assert (
            render_video(engine, settings, session.public_id).run.id == run.id
        )
    forced = render_video(engine, settings, session.public_id, force=True).run
    assert forced.id != run.id and forced.output_path != run.output_path
    assert (settings.paths.data / run.output_path).is_file()
    settings.media.video.fps = 12
    changed = render_video(engine, settings, session.public_id).run
    assert changed.input_fingerprint != forced.input_fingerprint
    assert changed.frame_rate == pytest.approx(12)
    assert len(video_rows(engine)) == 3
    assert not list((workspace / "intermediate").iterdir())
    with create_session_factory(engine)() as db:
        assert (
            db.get(Session, session.id).status == SessionStatus.VIDEO_RENDERED
        )


def test_new_music_rejects_stale_audio_then_fresh_audio_succeeds(
    video_session, media_files
):
    settings, engine, config, session, _ = video_session
    other = media_files[0].with_name("second.wav")
    other.write_bytes(media_files[0].read_bytes() + b"different")
    import_assets(
        engine,
        settings.paths.data,
        session.public_id,
        other,
        AssetKind.MUSIC,
        Provenance(),
    )
    with pytest.raises(RenderError, match="No current successful audio"):
        render_video(engine, settings, session.public_id)
    assert not video_rows(engine)
    result = CliRunner().invoke(
        app, ["render", "video", session.public_id, "--config", str(config)]
    )
    assert (
        result.exit_code == 1
        and "No current successful audio" in result.output
    )
    current = render_audio(engine, settings, session.public_id).run
    assert (
        render_video(engine, settings, session.public_id).run.inputs_json[
            "audio_render"
        ]["render_id"]
        == current.public_id
    )


@pytest.mark.parametrize("target", ["artwork", "audio"])
@pytest.mark.parametrize("damage", ["changed", "missing", "symlink", "unsafe"])
def test_input_integrity_preflight(video_session, target, damage):
    settings, engine, config, session, audio = video_session
    with create_session_factory(engine).begin() as db:
        row = (
            db.get(RenderRun, audio.id)
            if target == "audio"
            else db.scalar(
                select(Asset).where(Asset.kind == AssetKind.ARTWORK)
            )
        )
        field = "output_path" if target == "audio" else "path"
        path = settings.paths.data / getattr(row, field)
        if damage == "unsafe":
            setattr(row, field, "../outside")
        elif damage == "changed":
            path.write_bytes(b"changed")
        elif damage == "missing":
            path.unlink()
        else:
            outside = settings.paths.data / "copy"
            outside.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(outside)
    with pytest.raises(RenderError):
        render_video(engine, settings, session.public_id)
    assert not video_rows(engine)
    assert (
        CliRunner()
        .invoke(
            app,
            ["render", "video", session.public_id, "--config", str(config)],
        )
        .exit_code
        == 1
    )


@pytest.mark.parametrize("count", [0, 2])
def test_exactly_one_artwork(video_session, count):
    settings, engine, config, session, _ = video_session
    with create_session_factory(engine).begin() as db:
        art = db.scalar(select(Asset).where(Asset.kind == AssetKind.ARTWORK))
        if count == 0:
            db.delete(art)
        else:
            db.add(
                Asset(
                    public_id="asset-other",
                    session_id=session.id,
                    kind=AssetKind.ARTWORK,
                    path="other.png",
                    original_filename="other.png",
                    sha256="other",
                    size_bytes=1,
                )
            )
    with pytest.raises(
        RenderError, match=f"exactly one artwork source; found {count}"
    ):
        render_video(engine, settings, session.public_id)
    assert not video_rows(engine)
    assert (
        CliRunner()
        .invoke(
            app,
            ["render", "video", session.public_id, "--config", str(config)],
        )
        .exit_code
        == 1
    )


@pytest.mark.parametrize("damage", ["changed", "missing", "symlink", "unsafe"])
def test_cached_video_integrity_requires_force(video_session, damage):
    settings, engine, _, session, _ = video_session
    first = render_video(engine, settings, session.public_id).run
    path = settings.paths.data / first.output_path
    if damage == "changed":
        path.write_bytes(b"changed")
    elif damage == "missing":
        path.unlink()
    elif damage == "symlink":
        copy = path.with_suffix(".copy")
        copy.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy)
    else:
        with create_session_factory(engine).begin() as db:
            db.get(RenderRun, first.id).output_path = "../unsafe.mp4"
    with pytest.raises(RenderError, match="--force"):
        render_video(engine, settings, session.public_id)
    assert len(video_rows(engine)) == 1
    assert (
        render_video(engine, settings, session.public_id, force=True).run.id
        != first.id
    )


@pytest.mark.parametrize("failure", ["ffmpeg", "artwork", "audio"])
def test_video_failure_retains_history_and_rechecks_inputs(
    video_session, monkeypatch, failure
):
    from swale_sounds.rendering.video_service import render_video_pipeline

    settings, engine, _, session, _ = video_session

    def pipeline(executable, artwork, audio, directory, config, duration, log):
        if failure == "ffmpeg":
            log.write("injected diagnostics\n")
            raise RenderError("injected FFmpeg failure")
        candidate = render_video_pipeline(
            executable, artwork, audio, directory, config, duration, log
        )
        (artwork if failure == "artwork" else audio).write_bytes(
            b"changed during render"
        )
        return candidate

    monkeypatch.setattr(
        "swale_sounds.rendering.video_service.render_video_pipeline", pipeline
    )
    with pytest.raises(RenderError, match="Video render failed"):
        render_video(engine, settings, session.public_id)
    row = video_rows(engine)[0]
    assert (
        row.status == RenderStatus.FAILED
        and row.error_message
        and row.finished_at
    )
    assert (settings.paths.data / row.log_path).is_file()
    workspace = settings.paths.data / session.workspace_path
    assert not list((workspace / "intermediate").iterdir())
    assert not list((workspace / "output").rglob("*.mp4"))
    with create_session_factory(engine)() as db:
        assert (
            db.get(Session, session.id).status == SessionStatus.AUDIO_RENDERED
        )


def test_video_cli_success_reuse_force_and_failures(
    video_session, monkeypatch
):
    _, engine, config, session, _ = video_session
    runner = CliRunner()
    args = ["render", "video", session.public_id, "--config", str(config)]
    assert runner.invoke(app, ["render", "video", "--help"]).exit_code == 0
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert "Rendered video" in first.output
    assert "Reusing" in runner.invoke(app, args).output
    assert runner.invoke(app, [*args, "--force"]).exit_code == 0
    assert len(video_rows(engine)) == 2
    missing = runner.invoke(
        app, ["render", "video", "unknown", "--config", str(config)]
    )
    assert missing.exit_code == 1 and "Session not found" in missing.output

    def failed(*args, **kwargs):
        raise RenderError("FFmpeg failure")

    monkeypatch.setattr(
        "swale_sounds.rendering.video_service.render_video_pipeline", failed
    )
    assert runner.invoke(app, [*args, "--force"]).exit_code == 1
    with create_session_factory(engine)() as db:
        assert (
            db.get(Session, session.id).status == SessionStatus.VIDEO_RENDERED
        )
    monkeypatch.setattr(
        "swale_sounds.rendering.ffmpeg.shutil.which", lambda _: None
    )
    assert runner.invoke(app, args).exit_code == 1


@pytest.mark.parametrize(
    "status",
    [SessionStatus.CREATED, SessionStatus.ASSETS_READY, SessionStatus.FAILED],
)
def test_video_cli_rejects_status(render_session, status):
    _, engine, config, session = render_session
    with create_session_factory(engine).begin() as db:
        db.get(Session, session.id).status = status
    result = CliRunner().invoke(
        app, ["render", "video", session.public_id, "--config", str(config)]
    )
    assert result.exit_code == 1 and "not ready for video" in result.output
    assert not video_rows(engine)


def test_video_does_not_read_music_bytes_and_accepts_historical_ffmpeg(
    video_session, monkeypatch
):
    from swale_sounds.rendering.service import input_fingerprint

    settings, engine, _, session, audio = video_session
    with create_session_factory(engine).begin() as db:
        run = db.get(RenderRun, audio.id)
        run.ffmpeg_version = "ffmpeg historical build"
        run.input_fingerprint = input_fingerprint(
            run.inputs_json, run.configuration_json, run.ffmpeg_version
        )
        music = db.scalar(select(Asset).where(Asset.kind == AssetKind.MUSIC))
        (settings.paths.data / music.path).unlink()
    assert (
        render_video(engine, settings, session.public_id).run.status
        == RenderStatus.SUCCEEDED
    )


def test_video_requires_audio_record_even_with_ready_status(render_session):
    _, engine, config, session = render_session
    with create_session_factory(engine).begin() as db:
        db.get(Session, session.id).status = SessionStatus.AUDIO_RENDERED
    result = CliRunner().invoke(
        app, ["render", "video", session.public_id, "--config", str(config)]
    )
    assert (
        result.exit_code == 1
        and "No current successful audio" in result.output
    )
    assert not video_rows(engine)


def test_running_video_is_reserved_before_ffmpeg_without_held_lock(
    video_session, monkeypatch
):
    settings, engine, _, session, _ = video_session

    def pipeline(*args, **kwargs):
        # A separate connection can write while the encoder is running.
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            assert (
                connection.exec_driver_sql(
                    "SELECT status FROM render_runs WHERE stage = 'video'"
                ).scalar_one()
                == "running"
            )
            connection.rollback()
        with pytest.raises(RenderError, match="already running"):
            render_video(engine, settings, session.public_id)
        raise RenderError("stop test attempt")

    monkeypatch.setattr(
        "swale_sounds.rendering.video_service.render_video_pipeline", pipeline
    )
    with pytest.raises(RenderError, match="stop test attempt"):
        render_video(engine, settings, session.public_id)
    assert len(video_rows(engine)) == 1


def test_video_commit_failure_removes_published_output(video_session):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    settings, engine, _, session, _ = video_session
    commits = 0

    def fail_commit(connection):
        nonlocal commits
        commits += 1
        if commits == 2:
            raise OperationalError(
                "COMMIT", {}, RuntimeError("final commit failure")
            )

    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(RenderError, match="final commit failure"):
            render_video(engine, settings, session.public_id)
    finally:
        event.remove(engine, "commit", fail_commit)
    run = video_rows(engine)[0]
    assert run.status == RenderStatus.FAILED and run.output_path is None
    assert not list(
        (settings.paths.data / session.workspace_path / "output").rglob(
            "*.mp4"
        )
    )
