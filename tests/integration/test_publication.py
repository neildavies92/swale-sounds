import json
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from typer.testing import CliRunner

from swale_sounds.assets.files import calculate_sha256
from swale_sounds.cli import app
from swale_sounds.database import create_session_factory
from swale_sounds.models import Asset, AssetKind, RenderRun, RenderStatus
from swale_sounds.publishing.models import PublicationError, PublicationPlan
from swale_sounds.publishing.service import create_publication_plan
from swale_sounds.rendering.service import render_audio
from swale_sounds.rendering.video_service import render_video


@pytest.fixture
def publication_session(render_session):
    settings, engine, config, session = render_session
    settings.media.video.width = 160
    settings.media.video.height = 90
    settings.media.video.fps = 10
    config.write_text(yaml.safe_dump(settings.model_dump(mode="json")))
    audio = render_audio(engine, settings, session.public_id).run
    video = render_video(engine, settings, session.public_id).run
    return settings, engine, config, session, audio, video


def production_state(engine):
    with engine.connect() as connection:
        return {
            table: connection.exec_driver_sql(f"SELECT * FROM {table}").all()
            for table in ("sessions", "assets", "render_runs")
        }


def test_deterministic_plan_snapshot_and_no_production_mutation(
    publication_session,
):
    settings, engine, _, session, audio, video = publication_session
    with create_session_factory(engine).begin() as db:
        asset = db.scalar(select(Asset).where(Asset.kind == AssetKind.MUSIC))
        asset.generation_parameters = {"api_key": "secret-response"}
        asset.generation_prompt = "/private/secret-prompt"
        asset.provider_plan = "private-plan"
    before = production_state(engine)
    workspace = settings.paths.data / session.workspace_path
    files = {
        p: calculate_sha256(p) for p in workspace.rglob("*") if p.is_file()
    }
    plan, path = create_publication_plan(engine, settings, session.public_id)
    content = path.read_bytes()
    assert PublicationPlan.model_validate_json(content) == plan
    assert plan.session_id == session.public_id
    assert plan.audio_render_id == audio.public_id
    assert plan.video.render_id == video.public_id
    assert plan.spec_sha256 == session.spec_sha256
    assert plan.content.model_dump(mode="json") == session.spec_json
    assert plan.video.sha256 == video.output_sha256
    assert (
        workspace / plan.video.path == settings.paths.data / video.output_path
    )
    assert len(plan.source_assets) == 2
    assert plan.music[0].asset_id in {s.asset_id for s in plan.source_assets}
    assert all(not Path(s.path).is_absolute() for s in plan.source_assets)
    assert str(settings.paths.data).encode() not in content
    assert (
        b"secret-response" not in content and b"secret-prompt" not in content
    )
    assert b"private-plan" not in content
    stamp = path.stat().st_mtime_ns
    assert (
        create_publication_plan(engine, settings, session.public_id)[0] == plan
    )
    assert path.read_bytes() == content and path.stat().st_mtime_ns == stamp
    assert production_state(engine) == before
    assert all(calculate_sha256(p) == digest for p, digest in files.items())
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "hash",
        "symlink",
        "path",
        "size",
        "width",
        "duration",
        "failed",
        "obsolete",
        "configuration",
        "spec",
        "music",
        "artwork",
        "audio_config",
        "audio_missing",
        "db_spec",
        "probe",
        "ffmpeg",
    ],
)
def test_stale_or_damaged_video_rejected(publication_session, damage):
    settings, engine, _, session, audio, video = publication_session
    path = settings.paths.data / video.output_path
    with create_session_factory(engine).begin() as db:
        run = db.get(RenderRun, video.id)
        if damage == "missing":
            path.unlink()
        elif damage == "hash":
            path.write_bytes(b"bad")
        elif damage == "symlink":
            copy = path.with_suffix(".copy")
            path.rename(copy)
            path.symlink_to(copy)
        elif damage == "path":
            run.output_path = "../outside.mp4"
        elif damage == "size":
            run.output_size_bytes += 1
        elif damage == "width":
            run.width += 2
        elif damage == "duration":
            run.duration_seconds += 1
        elif damage == "failed":
            run.status = RenderStatus.FAILED
        elif damage == "obsolete":
            run.input_fingerprint = "0" * 64
        elif damage == "configuration":
            settings.media.video.fps = 12
        elif damage == "audio_config":
            settings.media.audio.bitrate = "128k"
        elif damage == "probe":
            path.write_bytes(
                (settings.paths.data / audio.output_path).read_bytes()
            )
            run.output_sha256 = calculate_sha256(path)
            run.output_size_bytes = path.stat().st_size
        elif damage == "ffmpeg":
            run.ffmpeg_version = "different version"
        elif damage == "audio_missing":
            (settings.paths.data / audio.output_path).unlink()
        elif damage == "spec":
            (settings.paths.data / session.spec_path).write_text("changed")
        elif damage == "db_spec":
            from swale_sounds.models import Session

            row = db.get(Session, session.id)
            row.spec_json = {**row.spec_json, "spec_version": 2}
        else:
            kind = AssetKind.MUSIC if damage == "music" else AssetKind.ARTWORK
            asset = db.scalar(select(Asset).where(Asset.kind == kind))
            (settings.paths.data / asset.path).write_bytes(b"changed")
    with pytest.raises(PublicationError):
        create_publication_plan(engine, settings, session.public_id)
    assert not (
        settings.paths.data / session.workspace_path / "output/publish"
    ).exists()


def test_no_video_and_cli_error(render_session):
    settings, engine, config, session = render_session
    render_audio(engine, settings, session.public_id)
    with pytest.raises(PublicationError, match="render video"):
        create_publication_plan(engine, settings, session.public_id)
    result = CliRunner().invoke(
        app, ["publish", "plan", session.public_id, "--config", str(config)]
    )
    assert result.exit_code == 1 and "render video" in result.output


def test_valid_current_video_ignores_later_failed_or_obsolete(
    publication_session,
):
    settings, engine, _, session, _, first = publication_session
    later = render_video(engine, settings, session.public_id, force=True).run
    with create_session_factory(engine).begin() as db:
        db.get(RenderRun, later.id).status = RenderStatus.FAILED
    assert (
        create_publication_plan(engine, settings, session.public_id)[
            0
        ].video.render_id
        == first.public_id
    )
    with create_session_factory(engine).begin() as db:
        row = db.get(RenderRun, later.id)
        row.status = RenderStatus.SUCCEEDED
        row.input_fingerprint = "0" * 64
    assert (
        create_publication_plan(engine, settings, session.public_id)[
            0
        ].video.render_id
        == first.public_id
    )


def test_atomic_update_and_failed_replacement_preserves_plan(
    publication_session, monkeypatch
):
    settings, engine, _, session, _, _ = publication_session
    _, path = create_publication_plan(engine, settings, session.public_id)
    before = path.read_bytes()
    new = render_video(engine, settings, session.public_id, force=True).run
    import os

    original = os.replace

    def interrupted(source, destination):
        assert Path(source).read_bytes() != before
        assert Path(destination).read_bytes() == before
        raise OSError("interrupted replace")

    with monkeypatch.context() as patch:
        patch.setattr(
            "swale_sounds.publishing.service.os.replace", interrupted
        )
        with pytest.raises(PublicationError, match="interrupted"):
            create_publication_plan(engine, settings, session.public_id)
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]
    calls = []

    def checked_replace(source, destination):
        calls.append(destination)
        original(source, destination)

    monkeypatch.setattr(
        "swale_sounds.publishing.service.os.replace", checked_replace
    )
    plan, _ = create_publication_plan(engine, settings, session.public_id)
    assert plan.video.render_id == new.public_id and calls == [path]


@pytest.mark.parametrize(
    "obstacle", ["unrelated", "symlink", "directory", "edited"]
)
def test_existing_package_protected(publication_session, obstacle):
    settings, engine, _, session, _, _ = publication_session
    _, path = create_publication_plan(engine, settings, session.public_id)
    if obstacle == "unrelated":
        path.write_text('{"unrelated": true}')
    elif obstacle == "edited":
        path.write_text(json.dumps(json.loads(path.read_text())))
    else:
        path.unlink()
        if obstacle == "directory":
            path.mkdir()
        else:
            path.symlink_to(settings.paths.data / session.spec_path)
    with pytest.raises(PublicationError):
        create_publication_plan(engine, settings, session.public_id)


@pytest.mark.parametrize(
    "args", [["publish", "--help"], ["publish", "plan", "--help"]]
)
def test_cli_help(args):
    assert CliRunner().invoke(app, args).exit_code == 0


def test_new_source_rejects_stale_audio_and_preserves_package(
    publication_session, media_files
):
    from swale_sounds.assets.provenance import Provenance
    from swale_sounds.assets.service import import_assets

    settings, engine, _, session, _, _ = publication_session
    _, path = create_publication_plan(engine, settings, session.public_id)
    before = path.read_bytes()
    extra = media_files[0].with_name("extra.wav")
    extra.write_bytes(media_files[0].read_bytes() + b"different")
    import_assets(
        engine,
        settings.paths.data,
        session.public_id,
        extra,
        AssetKind.MUSIC,
        Provenance(),
    )
    with pytest.raises(PublicationError, match="render audio"):
        create_publication_plan(engine, settings, session.public_id)
    assert path.read_bytes() == before


def test_licence_change_updates_plan_without_rendering(publication_session):
    settings, engine, _, session, _, video = publication_session
    _, path = create_publication_plan(engine, settings, session.public_id)
    before = path.read_bytes()
    with create_session_factory(engine).begin() as db:
        asset = db.scalar(select(Asset).where(Asset.kind == AssetKind.MUSIC))
        asset.licence_notes = "Attribution: required"
    with pytest.raises(PublicationError, match="credit is missing"):
        create_publication_plan(engine, settings, session.public_id)
    assert path.read_bytes() == before
    with create_session_factory(engine).begin() as db:
        asset = db.scalar(select(Asset).where(Asset.kind == AssetKind.MUSIC))
        asset.licence_notes += "\nAttribution text: Song by Artist"
    plan, _ = create_publication_plan(engine, settings, session.public_id)
    assert plan.video.render_id == video.public_id
    assert "Song by Artist" in plan.description


def test_concurrent_plans_are_identical(publication_session):
    from concurrent.futures import ThreadPoolExecutor

    settings, engine, _, session, _, _ = publication_session
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                create_publication_plan, engine, settings, session.public_id
            )
            for _ in range(2)
        ]
        first, second = [future.result() for future in futures]
    assert first == second
    assert list(first[1].parent.iterdir()) == [first[1]]


def test_package_ancestor_symlink_rejected(publication_session, tmp_path):
    settings, engine, _, session, _, _ = publication_session
    outside = tmp_path / "outside"
    outside.mkdir()
    package = settings.paths.data / session.workspace_path / "output/publish"
    package.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PublicationError, match="Symbolic links"):
        create_publication_plan(engine, settings, session.public_id)
    assert not list(outside.iterdir())


def test_unknown_session_cli(session_environment):
    _, _, config = session_environment
    result = CliRunner().invoke(
        app, ["publish", "plan", "missing", "--config", str(config)]
    )
    assert result.exit_code == 1 and "Session not found" in result.output
