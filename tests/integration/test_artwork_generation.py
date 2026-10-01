import base64
import json
from unittest.mock import MagicMock

import pytest
import yaml
from openai.types import ImagesResponse
from typer.testing import CliRunner

from swale_sounds.assets.files import calculate_sha256
from swale_sounds.assets.provenance import AssetError, Provenance
from swale_sounds.assets.service import import_assets, list_assets
from swale_sounds.cli import app
from swale_sounds.database import create_session_factory
from swale_sounds.generation.artwork import generate_artwork, prepare_artwork
from swale_sounds.generation.openai_images import GenerationError
from swale_sounds.models import AssetKind, Session, SessionStatus
from swale_sounds.sessions.service import get_session


@pytest.fixture
def sdk(monkeypatch, media_files):
    client = MagicMock()
    client.__enter__.return_value = client
    client.images.generate.return_value = ImagesResponse(
        created=1,
        data=[
            {
                "b64_json": base64.b64encode(
                    media_files[1].read_bytes()
                ).decode(),
                "revised_prompt": "provider revised prompt",
            }
        ],
        output_format="png",
        size="2x3",
        quality="medium",
        background="opaque",
    )
    monkeypatch.setattr(
        "swale_sounds.generation.openai_images.OpenAI",
        MagicMock(return_value=client),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-credential")
    return client


def invoke(config, session_id, *options):
    return CliRunner().invoke(
        app,
        ["generate", "artwork", session_id, "--config", str(config), *options],
    )


def snapshot(root):
    return {
        p.relative_to(root): p.read_bytes() if p.is_file() else None
        for p in root.rglob("*")
    }


@pytest.mark.parametrize("music_first", [False, True])
def test_generated_artwork_uses_real_importer_and_lifecycle(
    asset_session, media_files, sdk, music_first
):
    settings, engine, _, session = asset_session
    if music_first:
        import_assets(
            engine,
            settings.paths.data,
            session.public_id,
            media_files[0],
            AssetKind.MUSIC,
            Provenance(),
        )
    plan = prepare_artwork(engine, settings, session.public_id)
    result = generate_artwork(
        engine,
        settings,
        session.public_id,
        licence_notes="operator evidence",
        licence_url="https://example.org/terms",
        licence_version="v1",
    )
    sdk.images.generate.assert_called_once()
    asset = result.asset
    assert asset.kind == AssetKind.ARTWORK and asset.provider == "openai"
    assert asset.provider_model == settings.generation.artwork.model
    assert asset.provider_plan is None
    assert asset.generation_prompt == plan.prompt
    assert asset.licence_notes == "operator evidence"
    assert (
        asset.licence_url == "https://example.org/terms"
        and asset.licence_version == "v1"
    )
    assert asset.sha256 == calculate_sha256(settings.paths.data / asset.path)
    assert asset.codec_name == "png" and (asset.width, asset.height) == (2, 3)
    assert asset.generation_parameters["request"]["size"] == "1536x864"
    assert (
        asset.generation_parameters["response"]["revised_prompt"]
        == "provider revised prompt"
    )
    assert "b64_json" not in json.dumps(asset.generation_parameters)
    manifest = json.loads(result.manifest.read_bytes())
    record = next(
        a for a in manifest["assets"] if a["asset_id"] == asset.public_id
    )
    assert record["generation_prompt"] == plan.prompt
    assert record["sha256"] == asset.sha256 and record["provider"] == "openai"
    assert "unit-test-credential" not in result.manifest.read_text()
    assert get_session(engine, session.public_id).status == (
        SessionStatus.ASSETS_READY if music_first else SessionStatus.CREATED
    )
    assert not list(
        (
            settings.paths.data / session.workspace_path / "intermediate"
        ).iterdir()
    )


def test_dry_run_is_credential_free_read_only(asset_session, monkeypatch, sdk):
    settings, engine, config, session = asset_session
    monkeypatch.delenv("OPENAI_API_KEY")
    before = snapshot(config.parent)
    result = invoke(config, session.public_id, "--dry-run")
    assert result.exit_code == 0, result.output
    for content in (
        session.public_id,
        "openai",
        settings.generation.artwork.model,
        "1536x864",
        "medium",
        "png",
        "Prompt:",
        "Scene:",
        "Timeout:",
    ):
        assert content in result.output
    assert "OPENAI_API_KEY" not in result.output
    sdk.images.generate.assert_not_called()
    assert snapshot(config.parent) == before
    assert list_assets(engine, session.public_id) == []


def test_exact_custom_prompt_is_used_and_preserved(
    asset_session, sdk, tmp_path, monkeypatch
):
    _, engine, config, session = asset_session
    source = tmp_path / "prompt.txt"
    contents = "  Café à Paris 東京\r\nPreserve whitespace.\n  "
    source.write_bytes(contents.encode())

    def not_used(*args):
        pytest.fail("Custom prompt must bypass the template")

    monkeypatch.setattr(
        "swale_sounds.generation.artwork.build_prompt", not_used
    )
    result = invoke(config, session.public_id, "--prompt-file", str(source))
    assert result.exit_code == 0, result.output
    asset = list_assets(engine, session.public_id)[0]
    assert asset.generation_prompt == contents
    assert sdk.images.generate.call_args.kwargs["prompt"] == contents
    assert source.read_bytes() == contents.encode()
    assert (
        asset.licence_notes is None
        and asset.licence_url is None
        and asset.licence_version is None
    )


@pytest.mark.parametrize(
    "content", [None, b"\xff", b"", b" \n\t", b"x" * 32001]
)
def test_bad_prompt_files_fail_before_paid_call(
    asset_session, sdk, tmp_path, content
):
    _, engine, config, session = asset_session
    source = tmp_path / "bad-prompt.txt"
    if content is not None:
        source.write_bytes(content)
    result = invoke(config, session.public_id, "--prompt-file", str(source))
    assert result.exit_code == 1 and "Traceback" not in result.output
    sdk.images.generate.assert_not_called()
    assert list_assets(engine, session.public_id) == []


@pytest.mark.parametrize(
    "problem",
    [
        "unknown",
        "assets_ready",
        "audio_rendered",
        "video_rendered",
        "failed",
        "disk",
        "spec_json",
        "spec_hash",
        "spec_symlink",
        "artwork",
        "destination",
        "staging_symlink",
    ],
)
def test_invalid_preflight_avoids_paid_call(
    asset_session, sdk, media_files, problem
):
    settings, engine, config, session = asset_session
    public_id = session.public_id
    workspace = settings.paths.data / session.workspace_path
    if problem == "unknown":
        public_id = "session-999999"
    elif problem in {
        "assets_ready",
        "audio_rendered",
        "video_rendered",
        "failed",
    }:
        with create_session_factory(engine).begin() as db:
            db.get(Session, session.id).status = SessionStatus(problem)
    elif problem == "disk":
        (workspace / "session.yaml").write_text("tampered")
    elif problem in {"spec_json", "spec_hash"}:
        with create_session_factory(engine).begin() as db:
            row = db.get(Session, session.id)
            if problem == "spec_json":
                row.spec_json = {}
            else:
                row.spec_sha256 = "invalid"
    elif problem == "spec_symlink":
        path = workspace / "session.yaml"
        other = config.parent / "other.yaml"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    elif problem == "artwork":
        import_assets(
            engine,
            settings.paths.data,
            public_id,
            media_files[1],
            AssetKind.ARTWORK,
            Provenance(),
        )
    elif problem == "destination":
        (workspace / "artwork/source/generated-artwork.png").write_bytes(
            b"untracked"
        )
    else:
        (workspace / "intermediate").rmdir()
        (workspace / "intermediate").symlink_to(
            config.parent, target_is_directory=True
        )
    result = invoke(config, public_id)
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    sdk.images.generate.assert_not_called()


def test_missing_credentials_and_probe_avoid_provider(
    asset_session, sdk, monkeypatch
):
    settings, engine, config, session = asset_session
    monkeypatch.delenv("OPENAI_API_KEY")
    result = invoke(config, session.public_id)
    assert (
        result.exit_code == 1 and "OPENAI_API_KEY is required" in result.output
    )
    monkeypatch.setattr(
        "swale_sounds.generation.artwork.shutil.which", lambda _: None
    )
    result = invoke(config, session.public_id)
    assert result.exit_code == 1 and "ffprobe is required" in result.output
    sdk.images.generate.assert_not_called()
    assert list_assets(engine, session.public_id) == []
    assert not list(
        (
            settings.paths.data / session.workspace_path / "intermediate"
        ).iterdir()
    )


@pytest.mark.parametrize(
    "failure",
    [
        "provider",
        "decoding",
        "import",
        "manifest",
        "concurrent_artwork",
        "late_artwork",
        "changed_spec",
    ],
)
def test_generation_failure_cleanup_or_paid_candidate_recovery(
    asset_session, sdk, media_files, monkeypatch, failure
):
    settings, engine, config, session = asset_session
    workspace = settings.paths.data / session.workspace_path
    if failure == "provider":

        def fail(*args):
            raise GenerationError("provider failed")

        monkeypatch.setattr(
            "swale_sounds.generation.artwork.generate_image", fail
        )
    elif failure == "decoding":
        sdk.images.generate.return_value = ImagesResponse(
            created=1, data=[{"b64_json": "invalid!"}]
        )
    elif failure in {"import", "manifest"}:

        def fail(*args, **kwargs):
            raise AssetError("injected failure")

        target = (
            "swale_sounds.generation.artwork.import_assets"
            if failure == "import"
            else "swale_sounds.assets.service.regenerate_manifest"
        )
        monkeypatch.setattr(target, fail)
    elif failure in {"concurrent_artwork", "changed_spec"}:
        response = sdk.images.generate.return_value

        def change(**kwargs):
            if failure == "concurrent_artwork":
                import_assets(
                    engine,
                    settings.paths.data,
                    session.public_id,
                    media_files[1],
                    AssetKind.ARTWORK,
                    Provenance(),
                )
            else:
                (workspace / "session.yaml").write_text(
                    "changed during request"
                )
            return response

        sdk.images.generate.side_effect = change
    else:

        def late_import(*args, **kwargs):
            import_assets(
                engine,
                settings.paths.data,
                session.public_id,
                media_files[1],
                AssetKind.ARTWORK,
                Provenance(),
            )
            return import_assets(*args, **kwargs)

        monkeypatch.setattr(
            "swale_sounds.generation.artwork.import_assets", late_import
        )
    result = invoke(config, session.public_id)
    assert result.exit_code == 1, result.output
    assert "Generated artwork for" not in result.output
    candidates = list(
        (workspace / "intermediate").glob("generation-*/generated-artwork.png")
    )
    if failure in {"provider", "decoding"}:
        assert not list((workspace / "intermediate").iterdir())
        assert list_assets(engine, session.public_id) == []
    else:
        assert len(candidates) == 1
        assert candidates[0].read_bytes() == media_files[1].read_bytes()
        assert str(candidates[0]) in result.output
    assert (
        get_session(engine, session.public_id).status == SessionStatus.CREATED
    )
    if failure in {"concurrent_artwork", "late_artwork", "manifest"}:
        assert len(list_assets(engine, session.public_id)) == 1
        if failure == "manifest":
            assert "Assets committed" in result.output


def test_cli_help_success_and_second_artwork_rejection(asset_session, sdk):
    _, engine, config, session = asset_session
    runner = CliRunner()
    assert runner.invoke(app, ["generate", "--help"]).exit_code == 0
    assert runner.invoke(app, ["generate", "artwork", "--help"]).exit_code == 0
    result = invoke(config, session.public_id)
    assert result.exit_code == 0, result.output
    for value in (
        "Generated artwork",
        "Provider: openai",
        "Asset: asset-",
        "generated-artwork.png",
        "Manifest:",
    ):
        assert value in result.output
    result = invoke(config, session.public_id)
    assert result.exit_code == 1 and "already has an artwork" in result.output
    sdk.images.generate.assert_called_once()
    assert len(list_assets(engine, session.public_id)) == 1


def test_non_image_provider_bytes_are_retained_by_import_validation(
    asset_session, sdk
):
    settings, engine, config, session = asset_session
    sdk.images.generate.return_value = ImagesResponse(
        created=1,
        data=[{"b64_json": base64.b64encode(b"not an image").decode()}],
    )
    result = invoke(config, session.public_id)
    assert result.exit_code == 1 and "retained at" in result.output
    candidates = list(
        (settings.paths.data / session.workspace_path / "intermediate").glob(
            "generation-*/generated-artwork.png"
        )
    )
    assert len(candidates) == 1
    assert candidates[0].read_bytes() == b"not an image"
    assert list_assets(engine, session.public_id) == []


def test_dry_run_missing_database_creates_no_files(session_environment):
    _, _, config = session_environment
    data = yaml.safe_load(config.read_text())
    nonexistent = config.parent / "missing.db"
    data["database"]["url"] = f"sqlite:///{nonexistent}"
    config.write_text(yaml.safe_dump(data))
    before = snapshot(config.parent)
    result = invoke(config, "session-000001", "--dry-run")
    assert result.exit_code == 1 and "database is missing" in result.output
    assert not nonexistent.exists() and snapshot(config.parent) == before


def test_generated_artwork_feeds_existing_audio_video_pipeline(
    asset_session, sdk, media_files
):
    from swale_sounds.rendering.service import render_audio
    from swale_sounds.rendering.video_service import render_video
    from swale_sounds.sessions.schema import (
        SessionSpec,
        canonical_bytes,
        sha256_bytes,
    )

    settings, engine, _, session = asset_session
    with create_session_factory(engine).begin() as db:
        row = db.get(Session, session.id)
        spec = SessionSpec.model_validate(row.spec_json)
        spec.output.duration_minutes = 1
        contents = canonical_bytes(spec)
        row.spec_json = spec.model_dump(mode="json")
        row.spec_sha256 = sha256_bytes(contents)
        (settings.paths.data / row.spec_path).write_bytes(contents)
    settings.media.video.width = 160
    settings.media.video.height = 90
    settings.media.video.fps = 10
    import_assets(
        engine,
        settings.paths.data,
        session.public_id,
        media_files[0],
        AssetKind.MUSIC,
        Provenance(),
    )
    generated = generate_artwork(engine, settings, session.public_id)
    render_audio(engine, settings, session.public_id)
    video = render_video(engine, settings, session.public_id).run
    assert (
        video.inputs_json["artwork"]["asset_id"] == generated.asset.public_id
    )
    assert (
        get_session(engine, session.public_id).status
        == SessionStatus.VIDEO_RENDERED
    )
