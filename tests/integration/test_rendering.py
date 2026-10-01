import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from swale_sounds.assets.files import calculate_sha256
from swale_sounds.assets.probe import probe_media
from swale_sounds.assets.provenance import Provenance
from swale_sounds.assets.service import import_assets, list_assets
from swale_sounds.cli import app
from swale_sounds.database import create_session_factory
from swale_sounds.models import (
    Asset,
    AssetKind,
    RenderRun,
    RenderStatus,
    Session,
    SessionStatus,
)
from swale_sounds.rendering.audio import (
    AudioSettings,
    render_pipeline,
    verify_output,
)
from swale_sounds.rendering.ffmpeg import RenderError, find_ffmpeg
from swale_sounds.rendering.service import render_audio, reserve_render
from swale_sounds.sessions.service import get_session


def runs(engine):
    with create_session_factory(engine)() as db:
        return list(db.scalars(select(RenderRun).order_by(RenderRun.id)))


def test_real_render_reuse_force_and_changed_inputs(
    render_session, media_files, monkeypatch
):
    settings, engine, _, session = render_session
    sources = list_assets(engine, session.public_id)
    manifest = (
        settings.paths.data / session.workspace_path / "manifests/assets.json"
    )
    original_manifest = manifest.read_bytes()
    first = render_audio(engine, settings, session.public_id)
    run = first.run
    assert not first.reused
    assert run.status == RenderStatus.SUCCEEDED
    assert run.finished_at >= run.started_at
    assert run.started_at.utcoffset().total_seconds() == 0
    assert (
        get_session(engine, session.public_id).status
        == SessionStatus.AUDIO_RENDERED
    )
    output = settings.paths.data / run.output_path
    assert calculate_sha256(output) == run.output_sha256
    assert output.stat().st_size == run.output_size_bytes
    assert run.codec_name == "aac"
    assert run.sample_rate == settings.media.sample_rate
    assert run.channels == settings.media.channels
    assert run.duration_seconds == pytest.approx(60, abs=0.25)
    assert probe_media(output).streams[0].codec_name == "aac"
    assert run.inputs_json["spec_sha256"] == session.spec_sha256
    assert run.inputs_json["ambience"] is None
    assert run.ffmpeg_version.startswith("ffmpeg version")
    assert run.renderer_version == 1
    assert not (
        settings.paths.data
        / session.workspace_path
        / "intermediate"
        / run.public_id
    ).exists()
    assert (settings.paths.data / run.log_path).is_file()
    with monkeypatch.context() as patch:

        def forbidden(*args, **kwargs):
            pytest.fail("reuse invoked the FFmpeg render pipeline")

        patch.setattr(
            "swale_sounds.rendering.service.render_pipeline", forbidden
        )
        reused = render_audio(engine, settings, session.public_id)
        assert reused.reused and reused.run.public_id == run.public_id
        assert len(runs(engine)) == 1
    forced = render_audio(engine, settings, session.public_id, force=True)
    assert forced.run.public_id != run.public_id
    assert forced.run.output_path != run.output_path
    assert calculate_sha256(output) == run.output_sha256
    assert manifest.read_bytes() == original_manifest
    for asset in sources:
        assert (
            calculate_sha256(settings.paths.data / asset.path) == asset.sha256
        )
    additional = media_files[0].with_name("01-new.wav")
    additional.write_bytes(media_files[0].read_bytes() + b"different")
    import_assets(
        engine,
        settings.paths.data,
        session.public_id,
        additional,
        AssetKind.MUSIC,
        Provenance(),
    )
    changed = render_audio(engine, settings, session.public_id)
    assert changed.run.input_fingerprint != run.input_fingerprint
    assert len(changed.run.inputs_json["music"]) == 2
    assert [
        item["path"].split("/")[-1]
        for item in changed.run.inputs_json["music"]
    ] == ["01-new.wav", "track.wav"]
    assert changed.run.duration_seconds == pytest.approx(60, abs=0.25)
    assert len(runs(engine)) == 3
    assert len(list_assets(engine, session.public_id)) == 3


def test_ambience_and_nondefault_configuration(render_session, media_files):
    settings, engine, _, session = render_session
    import_assets(
        engine,
        settings.paths.data,
        session.public_id,
        media_files[0],
        AssetKind.AMBIENCE,
        Provenance(),
    )
    settings.media.sample_rate = 32000
    settings.media.channels = 1
    settings.media.audio.bitrate = "96k"
    settings.media.ambience.gain_db = -25
    result = render_audio(engine, settings, session.public_id)
    assert result.run.status == RenderStatus.SUCCEEDED
    assert result.run.inputs_json["ambience"]["asset_id"].startswith("asset-")
    assert result.run.configuration_json["ambience_gain_db"] == -25
    assert result.run.sample_rate == 32000
    assert result.run.channels == 1


@pytest.mark.parametrize(
    "damage", ["missing", "changed", "symlink", "unsafe", "hash"]
)
def test_source_integrity_failure_prevents_render_attempt(
    render_session, tmp_path, damage
):
    settings, engine, _, session = render_session
    source = next(
        item
        for item in list_assets(engine, session.public_id)
        if item.kind == AssetKind.MUSIC
    )
    path = settings.paths.data / source.path
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        path.write_bytes(b"changed")
    elif damage == "symlink":
        target = tmp_path / "target.wav"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    else:
        with create_session_factory(engine).begin() as db:
            asset = db.get(Asset, source.id)
            if damage == "unsafe":
                asset.path = "../escape.wav"
            else:
                asset.sha256 = "wrong"
    with pytest.raises(RenderError):
        render_audio(engine, settings, session.public_id)
    assert runs(engine) == []


@pytest.mark.parametrize(
    "status",
    [
        SessionStatus.CREATED,
        SessionStatus.FAILED,
        SessionStatus.VIDEO_RENDERED,
    ],
)
def test_invalid_session_status_rejected(render_session, status):
    settings, engine, _, session = render_session
    with create_session_factory(engine).begin() as db:
        db.get(Session, session.id).status = status
    with pytest.raises(RenderError, match="not ready"):
        render_audio(engine, settings, session.public_id)
    assert runs(engine) == []


def test_missing_music_and_multiple_ambience_are_rejected(
    render_session, media_files
):
    settings, engine, _, session = render_session
    for index in range(2):
        path = media_files[0].with_name(f"rain{index}.wav")
        path.write_bytes(media_files[0].read_bytes() + bytes([index]))
        import_assets(
            engine,
            settings.paths.data,
            session.public_id,
            path,
            AssetKind.AMBIENCE,
            Provenance(),
        )
    with pytest.raises(RenderError, match="at most one ambience"):
        render_audio(engine, settings, session.public_id)
    with create_session_factory(engine).begin() as db:
        for row in db.scalars(
            select(Asset).where(Asset.kind == AssetKind.MUSIC)
        ):
            db.delete(row)
    with pytest.raises(RenderError, match="at least one music"):
        render_audio(engine, settings, session.public_id)


@pytest.mark.parametrize("damage", ["missing", "changed", "symlink", "unsafe"])
def test_cached_output_provenance_failure_requires_force(
    render_session, tmp_path, damage
):
    settings, engine, _, session = render_session
    run = render_audio(engine, settings, session.public_id).run
    path = settings.paths.data / run.output_path
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        path.write_bytes(b"changed")
    elif damage == "symlink":
        target = tmp_path / "output.m4a"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    else:
        with create_session_factory(engine).begin() as db:
            db.get(RenderRun, run.id).output_path = "../escape.m4a"
    with pytest.raises(RenderError, match=r"provenance.*--force"):
        render_audio(engine, settings, session.public_id)
    assert len(runs(engine)) == 1
    new = render_audio(engine, settings, session.public_id, force=True)
    assert new.run.public_id != run.public_id
    assert all(row.status == RenderStatus.SUCCEEDED for row in runs(engine))


def test_ffmpeg_failure_is_persisted_without_session_failure(
    render_session, monkeypatch
):
    settings, engine, _, session = render_session

    def fail(*args, **kwargs):
        raise RenderError("injected FFmpeg failure")

    monkeypatch.setattr("swale_sounds.rendering.audio.run_ffmpeg", fail)
    with pytest.raises(RenderError, match="injected FFmpeg failure"):
        render_audio(engine, settings, session.public_id)
    run = runs(engine)[0]
    assert run.status == RenderStatus.FAILED
    assert run.finished_at is not None
    assert "injected" in run.error_message
    assert (settings.paths.data / run.log_path).is_file()
    assert run.output_path is None
    assert (
        get_session(engine, session.public_id).status
        == SessionStatus.ASSETS_READY
    )
    assert not (
        settings.paths.data
        / session.workspace_path
        / "intermediate"
        / run.public_id
    ).exists()


def test_active_render_prevents_competing_attempt(render_session):
    settings, engine, _, session = render_session
    reserve_render(engine, settings, session.public_id, False)
    with pytest.raises(RenderError, match="already running"):
        render_audio(engine, settings, session.public_id, force=True)
    assert len(runs(engine)) == 1


def test_pipeline_trims_longer_sequence(media_files, tmp_path):
    import wave

    source = media_files[0]
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 8000 * 3)
    settings = AudioSettings(
        target_duration_seconds=1,
        sample_rate=32000,
        channels=1,
        codec="aac",
        bitrate="96k",
        ambience_gain_db=-18,
    )
    directory = tmp_path / "trim"
    directory.mkdir()
    executable, _ = find_ffmpeg()
    with (tmp_path / "trim.log").open("w") as log:
        output = render_pipeline(
            executable, [source], None, directory, settings, log
        )
    assert verify_output(output, settings)[1] == pytest.approx(1, abs=0.25)


def test_render_cli_success_reuse_force_and_errors(
    render_session, monkeypatch
):
    _, _, config, session = render_session
    runner = CliRunner()

    def invoke(*arguments):
        return runner.invoke(
            app, ["render", "audio", *arguments, "--config", str(config)]
        )

    for arguments in (["render", "--help"], ["render", "audio", "--help"]):
        assert runner.invoke(app, arguments).exit_code == 0
    first = invoke(session.public_id)
    assert first.exit_code == 0, first.output
    assert "Rendered audio" in first.output
    assert "SHA-256" in first.output
    reused = invoke(session.public_id)
    assert reused.exit_code == 0
    assert "Reusing" in reused.output
    forced = invoke(session.public_id, "--force")
    assert forced.exit_code == 0
    assert "Rendered audio" in forced.output
    unknown = invoke("session-999999")
    assert unknown.exit_code == 1
    assert "Session not found" in unknown.output
    with monkeypatch.context() as patch:
        patch.setattr(
            "swale_sounds.rendering.ffmpeg.shutil.which", lambda _: None
        )
        missing = invoke(session.public_id)
        assert missing.exit_code == 1
        assert "ffmpeg was not found" in missing.output

    def fail(*args, **kwargs):
        raise RenderError("injected failure")

    monkeypatch.setattr("swale_sounds.rendering.audio.run_ffmpeg", fail)
    failed = invoke(session.public_id, "--force")
    assert failed.exit_code == 1
    assert "Log:" in failed.output
    assert "Traceback" not in failed.output


def test_finalization_failure_retains_failed_run_and_removes_output(
    render_session,
):
    settings, engine, _, session = render_session
    count = 0

    def fail_success_commit(connection):
        nonlocal count
        count += 1
        if count == 2:
            raise OperationalError(
                "COMMIT", {}, RuntimeError("injected final commit failure")
            )

    event.listen(engine, "commit", fail_success_commit)
    try:
        with pytest.raises(RenderError, match="final commit failure"):
            render_audio(engine, settings, session.public_id)
    finally:
        event.remove(engine, "commit", fail_success_commit)
    run = runs(engine)[0]
    assert run.status == RenderStatus.FAILED
    assert run.output_path is None
    assert not list(
        (settings.paths.data / session.workspace_path / "output/audio").glob(
            "*.m4a"
        )
    )
    assert (
        get_session(engine, session.public_id).status
        == SessionStatus.ASSETS_READY
    )


def test_source_mutation_during_render_rejects_output(
    render_session, monkeypatch
):
    settings, engine, _, session = render_session
    original = render_pipeline

    def mutate(executable, music, ambience, directory, config, log):
        output = original(executable, music, ambience, directory, config, log)
        music[0].write_bytes(b"changed during rendering")
        return output

    monkeypatch.setattr(
        "swale_sounds.rendering.service.render_pipeline", mutate
    )
    with pytest.raises(RenderError, match="Source changed during rendering"):
        render_audio(engine, settings, session.public_id)
    assert runs(engine)[0].status == RenderStatus.FAILED


def test_unsupported_codec_and_cli_invalid_state(render_session):
    settings, engine, config, session = render_session
    settings.media.audio.codec = "flac"
    with pytest.raises(RenderError, match="Unsupported audio codec"):
        render_audio(engine, settings, session.public_id)
    with create_session_factory(engine).begin() as db:
        db.get(Session, session.id).status = SessionStatus.CREATED
    result = CliRunner().invoke(
        app, ["render", "audio", session.public_id, "--config", str(config)]
    )
    assert result.exit_code == 1
    assert "not ready" in result.output


def test_render_does_not_read_artwork(render_session):
    settings, engine, _, session = render_session
    artwork = next(
        item
        for item in list_assets(engine, session.public_id)
        if item.kind == AssetKind.ARTWORK
    )
    (settings.paths.data / artwork.path).unlink()
    assert (
        render_audio(engine, settings, session.public_id).run.status
        == RenderStatus.SUCCEEDED
    )
