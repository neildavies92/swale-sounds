import base64
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError, OperationalError

from swale_sounds.assets.files import calculate_sha256
from swale_sounds.assets.manifest import regenerate_manifest
from swale_sounds.assets.probe import probe_media
from swale_sounds.assets.provenance import AssetError, Provenance
from swale_sounds.assets.service import import_assets, list_assets
from swale_sounds.database import create_session_factory
from swale_sounds.models import Asset, AssetKind, Session, SessionStatus
from swale_sounds.sessions.service import (
    SessionNotFoundError,
    create_session,
    get_session,
)


def do_import(environment, path, kind=AssetKind.MUSIC, provenance=None):
    settings, engine, _, row = environment
    return import_assets(
        engine,
        settings.paths.data,
        row.public_id,
        path,
        kind,
        provenance or Provenance(),
    )


def test_real_ffprobe_audio_and_artwork(media_files):
    music, artwork = media_files
    audio = probe_media(music)
    assert audio.streams[0].codec_type == "audio"
    assert audio.streams[0].sample_rate == 8000
    assert audio.streams[0].channels == 1
    assert audio.format.duration == pytest.approx(0.1, abs=0.01)
    image = probe_media(artwork, count_frames=True)
    assert (image.streams[0].width, image.streams[0].height) == (2, 3)
    assert image.streams[0].nb_read_frames == 1


def test_import_captures_bytes_metadata_provenance_and_relationship(
    asset_session, media_files
):
    settings, engine, _, session = asset_session
    music, _ = media_files
    original = music.read_bytes()
    result = do_import(
        asset_session,
        music,
        provenance=Provenance(
            provider="example",
            provider_model="v2",
            provider_plan="paid",
            generation_prompt="Café jazz",
            generation_parameters={"seed": 7},
            licence_notes="Supplied evidence",
            licence_url="https://example.org/terms",
            licence_version="v1",
        ),
    )
    assert result.imported == 1
    asset = list_assets(engine, session.public_id)[0]
    assert asset.public_id.startswith("asset-")
    assert asset.kind == AssetKind.MUSIC
    assert asset.path == "sessions/session-000001/music/source/track.wav"
    assert asset.original_filename == "track.wav"
    assert asset.size_bytes == len(original)
    assert asset.sha256 == calculate_sha256(music)
    assert asset.sha256 == calculate_sha256(settings.paths.data / asset.path)
    assert music.read_bytes() == original
    assert asset.sample_rate == 8000
    assert asset.channels == 1
    assert asset.duration_seconds == pytest.approx(0.1)
    assert asset.codec_name == "pcm_s16le"
    assert asset.format_name == "wav"
    assert asset.mime_type in {"audio/x-wav", "audio/wav", "audio/vnd.wave"}
    assert asset.provider == "example"
    assert asset.provider_model == "v2"
    assert asset.provider_plan == "paid"
    assert asset.generation_prompt == "Café jazz"
    assert asset.generation_parameters == {"seed": 7}
    assert asset.licence_notes == "Supplied evidence"
    assert asset.licence_url == "https://example.org/terms"
    assert asset.licence_version == "v1"
    assert asset.created_at.utcoffset().total_seconds() == 0
    with create_session_factory(engine)() as db:
        saved = db.get(Asset, asset.id)
        assert saved.session.public_id == session.public_id
        assert db.get(Session, session.id).assets[0].id == asset.id


def test_directory_import_is_sorted_and_duplicate_content_is_skipped(
    asset_session, media_files, tmp_path
):
    music, _ = media_files
    source = tmp_path / "tracks"
    source.mkdir()
    (source / "z.wav").write_bytes(music.read_bytes())
    (source / "a.wav").write_bytes(music.read_bytes())
    different = bytearray(music.read_bytes())
    different[-1] = 1
    (source / "b.wav").write_bytes(different)
    result = do_import(asset_session, source)
    assert result.imported == 2
    assert result.skipped == ["z.wav"]
    assets = list_assets(asset_session[1], asset_session[3].public_id)
    assert [asset.original_filename for asset in assets] == ["a.wav", "b.wav"]


def test_duplicate_reimport_and_renamed_duplicate_are_idempotent(
    asset_session, media_files
):
    music, _ = media_files
    do_import(asset_session, music)
    result = do_import(
        asset_session, music, provenance=Provenance(provider="new")
    )
    assert result.imported == 0
    assert result.skipped == ["track.wav"]
    renamed = music.with_name("renamed.wav")
    renamed.write_bytes(music.read_bytes())
    result = do_import(asset_session, renamed)
    assert result.imported == 0
    assets = list_assets(asset_session[1], asset_session[3].public_id)
    assert len(assets) == 1
    assert assets[0].provider == "manual"


def test_filename_conflict_preserves_existing_bytes(
    asset_session, media_files
):
    settings, engine, _, session = asset_session
    music, _ = media_files
    do_import(asset_session, music)
    saved = list_assets(engine, session.public_id)[0]
    original = (settings.paths.data / saved.path).read_bytes()
    music.write_bytes(original + b"different")
    with pytest.raises(AssetError, match="filename conflict"):
        do_import(asset_session, music)
    assert (settings.paths.data / saved.path).read_bytes() == original
    assert len(list_assets(engine, session.public_id)) == 1


@pytest.mark.parametrize(
    "order",
    [
        [AssetKind.MUSIC, AssetKind.ARTWORK],
        [AssetKind.ARTWORK, AssetKind.MUSIC],
        [AssetKind.AMBIENCE, AssetKind.MUSIC, AssetKind.ARTWORK],
    ],
)
def test_readiness_requires_music_and_artwork_in_any_order(
    asset_session, media_files, order
):
    _, engine, _, session = asset_session
    music, artwork = media_files
    for index, kind in enumerate(order):
        do_import(
            asset_session,
            artwork if kind == AssetKind.ARTWORK else music,
            kind,
        )
        expected = (
            SessionStatus.ASSETS_READY
            if index == len(order) - 1
            else SessionStatus.CREATED
        )
        assert get_session(engine, session.public_id).status == expected


@pytest.mark.parametrize(
    "status",
    [
        SessionStatus.AUDIO_RENDERED,
        SessionStatus.VIDEO_RENDERED,
        SessionStatus.FAILED,
    ],
)
def test_import_never_downgrades_later_or_failed_state(
    asset_session, media_files, status
):
    _, engine, _, session = asset_session
    with create_session_factory(engine).begin() as db:
        db.get(Session, session.id).status = status
    do_import(asset_session, media_files[0])
    do_import(asset_session, media_files[1], AssetKind.ARTWORK)
    assert get_session(engine, session.public_id).status == status


def test_invalid_batch_leaves_no_new_rows_or_files(
    asset_session, media_files, tmp_path
):
    settings, engine, _, session = asset_session
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    (tracks / "a.wav").write_bytes(media_files[0].read_bytes())
    (tracks / "b.wav").write_bytes(media_files[0].read_bytes() + b"more")
    (tracks / "z.mp3").write_text("not audio", encoding="utf-8")
    with pytest.raises(AssetError, match="ffprobe"):
        do_import(asset_session, tracks)
    assert list_assets(engine, session.public_id) == []
    assert not list(
        (
            settings.paths.data / session.workspace_path / "music/source"
        ).iterdir()
    )
    assert (
        get_session(engine, session.public_id).status == SessionStatus.CREATED
    )


def test_image_with_audio_extension_is_rejected(asset_session, media_files):
    disguised = media_files[0].with_name("fake.mp3")
    disguised.write_bytes(media_files[1].read_bytes())
    with pytest.raises(AssetError, match="No audio stream"):
        do_import(asset_session, disguised)


def test_unknown_session_creates_no_state(asset_session, media_files):
    settings, engine, _, _ = asset_session
    before = set(settings.paths.data.rglob("*"))
    with pytest.raises(SessionNotFoundError):
        import_assets(
            engine,
            settings.paths.data,
            "session-999999",
            media_files[0],
            AssetKind.MUSIC,
            Provenance(),
        )
    assert set(settings.paths.data.rglob("*")) == before


@pytest.mark.parametrize("failure", ["insert", "commit", "copy", "hash"])
def test_mutation_failure_rolls_back_and_cleans_only_new_files(
    asset_session, media_files, monkeypatch, failure
):
    settings, engine, _, session = asset_session
    source = media_files[0]
    directory = settings.paths.data / session.workspace_path / "music/source"
    sentinel = directory / "user-data.txt"
    sentinel.write_text("keep", encoding="utf-8")

    def fail_commit(connection):
        raise OperationalError("COMMIT", {}, RuntimeError("injected failure"))

    if failure == "insert":
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TRIGGER reject_asset BEFORE INSERT ON assets "
                    "BEGIN SELECT RAISE(ABORT, 'injected'); END"
                )
            )
    elif failure == "commit":
        event.listen(engine, "commit", fail_commit)
    elif failure == "copy":

        def fail_copy(src, dst, **kwargs):
            dst.write(b"partial")
            raise OSError("injected copy failure")

        monkeypatch.setattr(
            "swale_sounds.assets.files.shutil.copyfileobj", fail_copy
        )
    else:
        monkeypatch.setattr(
            "swale_sounds.assets.files.calculate_sha256", lambda _: "bad"
        )
    try:
        with pytest.raises((AssetError, IntegrityError, OperationalError)):
            do_import(asset_session, source)
    finally:
        if failure == "commit":
            event.remove(engine, "commit", fail_commit)
    assert list_assets(engine, session.public_id) == []
    assert sorted(p.name for p in directory.iterdir()) == ["user-data.txt"]
    assert sentinel.read_text() == "keep"


def test_manifest_is_deterministic_complete_and_recoverable(
    asset_session, media_files
):
    settings, engine, _, session = asset_session
    do_import(
        asset_session,
        media_files[0],
        provenance=Provenance(
            provider="test", generation_parameters={"seed": 1}
        ),
    )
    result = do_import(asset_session, media_files[1], AssetKind.ARTWORK)
    original = result.manifest.read_bytes()
    manifest = json.loads(original)
    assert manifest["manifest_version"] == 1
    assert manifest["session_id"] == session.public_id
    assert [row["kind"] for row in manifest["assets"]] == [
        "artwork_source",
        "music_source",
    ]
    assets = list_assets(engine, session.public_id)
    assert [row["sha256"] for row in manifest["assets"]] == [
        row.sha256 for row in assets
    ]
    assert manifest["assets"][1]["generation_parameters"] == {"seed": 1}
    assert manifest["assets"][1]["provider"] == "test"
    assert manifest["assets"][0]["generation_prompt"] is None
    assert b"\r" not in original
    assert (
        regenerate_manifest(
            engine, settings.paths.data, session.public_id
        ).read_bytes()
        == original
    )
    result.manifest.write_text('{"tampered":true}', encoding="utf-8")
    assert (
        regenerate_manifest(
            engine, settings.paths.data, session.public_id
        ).read_bytes()
        == original
    )
    result.manifest.unlink()
    assert (
        regenerate_manifest(
            engine, settings.paths.data, session.public_id
        ).read_bytes()
        == original
    )


def test_manifest_failure_after_commit_preserves_assets_and_previous_manifest(
    asset_session, media_files, monkeypatch
):
    settings, engine, _, session = asset_session
    first = do_import(asset_session, media_files[0])
    original = first.manifest.read_bytes()

    def fail_replace(*args):
        raise OSError("injected manifest failure")

    with monkeypatch.context() as patch:
        patch.setattr("swale_sounds.assets.manifest.os.replace", fail_replace)
        with pytest.raises(
            AssetError, match=r"Assets committed.*asset manifest"
        ):
            do_import(asset_session, media_files[1], AssetKind.ARTWORK)
    assert len(list_assets(engine, session.public_id)) == 2
    assert (
        get_session(engine, session.public_id).status
        == SessionStatus.ASSETS_READY
    )
    assert first.manifest.read_bytes() == original
    assert list(first.manifest.parent.iterdir()) == [first.manifest]
    repaired = regenerate_manifest(
        engine, settings.paths.data, session.public_id
    )
    assert len(json.loads(repaired.read_bytes())["assets"]) == 2


def test_concurrent_duplicate_imports_are_serialized(
    asset_session, media_files
):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: do_import(asset_session, media_files[0]), range(2)
            )
        )
    assert sorted(result.imported for result in results) == [0, 1]
    assert len(list_assets(asset_session[1], asset_session[3].public_id)) == 1


@pytest.mark.parametrize(
    "extension", ["flac", "mp3", "m4a", "aac", "ogg", "jpg", "jpeg", "webp"]
)
def test_supported_formats_with_real_media(
    asset_session, media_files, extension
):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is needed to generate additional format fixtures")
    image = extension in {"jpg", "jpeg", "webp"}
    source = media_files[1] if image else media_files[0]
    converted = source.with_suffix(f".{extension}")
    if extension == "webp":
        # A one-pixel WebP avoids requiring an optional libwebp encoder.
        converted.write_bytes(
            base64.b64decode(
                "UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA"
            )
        )
    else:
        subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(source), str(converted)],
            check=True,
            capture_output=True,
            timeout=30,
        )
    result = do_import(
        asset_session,
        converted,
        AssetKind.ARTWORK if image else AssetKind.MUSIC,
    )
    assert result.imported == 1


def test_same_content_in_different_sessions_is_not_globally_deduplicated(
    asset_session, media_files, spec_file
):
    settings, engine, _, first = asset_session
    second = create_session(engine, settings.paths.data, spec_file)
    do_import(asset_session, media_files[0])
    result = import_assets(
        engine,
        settings.paths.data,
        second.public_id,
        media_files[0],
        AssetKind.MUSIC,
        Provenance(),
    )
    assert result.imported == 1
    first_asset = list_assets(engine, first.public_id)[0]
    second_asset = list_assets(engine, second.public_id)[0]
    assert first_asset.public_id != second_asset.public_id
    assert first_asset.path != second_asset.path
    assert first_asset.sha256 == second_asset.sha256


def test_late_copy_failure_cleans_entire_new_batch(
    asset_session, media_files, tmp_path, monkeypatch
):
    source = tmp_path / "batch"
    source.mkdir()
    (source / "a.wav").write_bytes(media_files[0].read_bytes())
    (source / "b.wav").write_bytes(media_files[0].read_bytes() + b"different")
    original_copy = shutil.copyfileobj
    count = 0

    def fail_second(src, dst, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            dst.write(b"partial")
            raise OSError("second file failed")
        original_copy(src, dst, **kwargs)

    monkeypatch.setattr(
        "swale_sounds.assets.files.shutil.copyfileobj", fail_second
    )
    with pytest.raises(AssetError, match="second file failed"):
        do_import(asset_session, source)
    settings, engine, _, session = asset_session
    assert list_assets(engine, session.public_id) == []
    assert not list(
        (
            settings.paths.data / session.workspace_path / "music/source"
        ).iterdir()
    )


def test_destination_symlink_is_rejected_without_touching_target(
    asset_session, media_files, tmp_path
):
    settings, engine, _, session = asset_session
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"important")
    path = (
        settings.paths.data / session.workspace_path / "music/source/track.wav"
    )
    path.symlink_to(outside)
    with pytest.raises(AssetError, match="Symbolic links"):
        do_import(asset_session, media_files[0])
    assert outside.read_bytes() == b"important"
    assert path.is_symlink()
    assert list_assets(engine, session.public_id) == []


def test_existing_duplicate_survives_failed_batch(
    asset_session, media_files, tmp_path
):
    do_import(asset_session, media_files[0])
    source = tmp_path / "batch"
    source.mkdir()
    (source / "a.wav").write_bytes(media_files[0].read_bytes())
    (source / "b.wav").write_bytes(media_files[0].read_bytes() + b"different")
    (source / "z.mp3").write_bytes(b"invalid")
    with pytest.raises(AssetError):
        do_import(asset_session, source)
    settings, engine, _, session = asset_session
    rows = list_assets(engine, session.public_id)
    assert len(rows) == 1
    assert (
        calculate_sha256(settings.paths.data / rows[0].path) == rows[0].sha256
    )
