import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from swale_sounds.assets.files import calculate_sha256
from swale_sounds.publishing.models import (
    PublicationError,
    SourceReference,
    ThumbnailSettings,
)
from swale_sounds.publishing.thumbnail import (
    create_thumbnail,
    thumbnail_fingerprint,
    verify_thumbnail_media,
)
from swale_sounds.rendering.ffmpeg import find_ffmpeg


@pytest.fixture
def thumbnail_source(tmp_path, media_files):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "output").mkdir()
    (workspace / "logs").mkdir()
    artwork = workspace / "source.png"
    artwork.write_bytes(media_files[1].read_bytes())
    return workspace, SourceReference(
        path="source.png",
        sha256=calculate_sha256(artwork),
        asset_id="asset-art",
    )


def test_real_thumbnail_and_reuse(thumbnail_source, monkeypatch):
    workspace, source = thumbnail_source
    before = (workspace / source.path).read_bytes()
    result = create_thumbnail(workspace, source)
    output = workspace / result.path
    assert output.read_bytes().startswith(b"\xff\xd8\xff")
    assert result.size_bytes == output.stat().st_size < 2000000
    assert result.sha256 == calculate_sha256(output)
    assert verify_thumbnail_media(output, result.settings) == result.size_bytes
    stamp = output.stat().st_mtime_ns

    def unexpected(*args):
        pytest.fail("valid cached thumbnail should not be encoded again")

    monkeypatch.setattr(
        "swale_sounds.publishing.thumbnail.run_ffmpeg", unexpected
    )
    assert create_thumbnail(workspace, source, result) == result
    assert output.stat().st_mtime_ns == stamp
    assert (workspace / source.path).read_bytes() == before
    assert not list(output.parent.glob(".thumbnail-*"))


def test_missing_output_restored_without_changing_identity(thumbnail_source):
    workspace, source = thumbnail_source
    result = create_thumbnail(workspace, source)
    (workspace / result.path).unlink()
    assert create_thumbnail(workspace, source, result) == result


@pytest.mark.parametrize("damage", ["tampered", "symlink", "unrelated"])
def test_output_never_clobbered(thumbnail_source, damage):
    workspace, source = thumbnail_source
    result = create_thumbnail(workspace, source)
    output = workspace / result.path
    if damage == "symlink":
        output.unlink()
        output.symlink_to(workspace / source.path)
    else:
        output.write_bytes(b"operator-owned contents")
    with pytest.raises(ValueError):
        create_thumbnail(
            workspace, source, None if damage == "unrelated" else result
        )
    assert (
        output.is_symlink()
        or output.read_bytes() == b"operator-owned contents"
    )


@pytest.mark.parametrize(
    "damage", ["missing", "changed", "symlink", "unsafe", "invalid"]
)
def test_source_integrity(thumbnail_source, damage):
    workspace, source = thumbnail_source
    path = workspace / source.path
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        path.write_bytes(b"bad")
    elif damage == "symlink":
        other = workspace / "copy.png"
        path.rename(other)
        path.symlink_to(other)
    elif damage == "unsafe":
        source = source.model_copy(update={"path": "../outside"})
    else:
        path.write_bytes(b"not media")
        source = source.model_copy(update={"sha256": calculate_sha256(path)})
    with pytest.raises(ValueError):
        create_thumbnail(workspace, source)


def test_settings_and_version_produce_new_immutable_artifacts(
    thumbnail_source, monkeypatch
):
    workspace, source = thumbnail_source
    first = create_thumbnail(workspace, source)
    old = (workspace / first.path).read_bytes()
    changed = create_thumbnail(
        workspace, source, first, ThumbnailSettings(quality=3)
    )
    assert changed.path != first.path
    executable, version = find_ffmpeg()
    monkeypatch.setattr(
        "swale_sounds.publishing.thumbnail.find_ffmpeg",
        lambda: (executable, version + " changed"),
    )
    another = create_thumbnail(workspace, source, first)
    assert another.fingerprint != first.fingerprint
    assert (workspace / first.path).read_bytes() == old


@pytest.mark.parametrize("failure", ["ffmpeg", "link", "size", "dimensions"])
def test_atomic_failure_cleanup(thumbnail_source, monkeypatch, failure):
    workspace, source = thumbnail_source

    def fail(*args):
        raise OSError("simulated interruption")

    if failure == "ffmpeg":
        monkeypatch.setattr(
            "swale_sounds.publishing.thumbnail.run_ffmpeg", fail
        )
    elif failure == "link":
        monkeypatch.setattr("swale_sounds.publishing.thumbnail.os.link", fail)
    elif failure == "size":

        def oversized(executable, args, log):
            Path(args[-1]).write_bytes(b"x" * 2000000)

        monkeypatch.setattr(
            "swale_sounds.publishing.thumbnail.run_ffmpeg", oversized
        )
    else:

        def wrong(executable, args, log):
            Path(args[-1]).write_bytes((workspace / source.path).read_bytes())

        monkeypatch.setattr(
            "swale_sounds.publishing.thumbnail.run_ffmpeg", wrong
        )
    with pytest.raises(PublicationError, match="Log:"):
        create_thumbnail(workspace, source)
    assert not list((workspace / "output/publish").iterdir())
    assert list((workspace / "logs").glob("thumbnail-*.log"))


@pytest.mark.parametrize("missing", ["ffmpeg", "ffprobe"])
def test_missing_media_tools(thumbnail_source, monkeypatch, missing):
    import shutil

    workspace, source = thumbnail_source
    original = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: None if name == missing else original(name),
    )
    with pytest.raises(ValueError, match="not found"):
        create_thumbnail(workspace, source)


def test_center_crop_preserves_aspect_ratio(thumbnail_source):
    workspace, source = thumbnail_source

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    pixels = b"".join(
        b"\0"
        + (b"\xff\0\0" if y < 28 else b"\0\0\xff" if y >= 100 else b"\0\xff\0")
        * 128
        for y in range(128)
    )
    image = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 128, 128, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", b"Comment\0private-metadata-marker")
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )
    (workspace / source.path).write_bytes(image)
    source = source.model_copy(
        update={"sha256": calculate_sha256(workspace / source.path)}
    )
    result = create_thumbnail(workspace, source)
    decoded = subprocess.run(
        [
            find_ffmpeg()[0],
            "-v",
            "error",
            "-i",
            str(workspace / result.path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    assert len(decoded) == 1280 * 720 * 3
    assert (
        b"private-metadata-marker"
        not in (workspace / result.path).read_bytes()
    )
    for x, y in [(10, 10), (1270, 710), (640, 360)]:
        r, g, b = decoded[(y * 1280 + x) * 3 : (y * 1280 + x) * 3 + 3]
        assert g > 230 and r < 20 and b < 20


def test_fingerprint_has_all_transform_inputs(thumbnail_source):
    _, source = thumbnail_source
    settings = ThumbnailSettings()
    original = thumbnail_fingerprint(source, settings, "ffmpeg A")
    assert original == thumbnail_fingerprint(source, settings, "ffmpeg A")
    for changed in (
        source.model_copy(update={"asset_id": "other"}),
        source.model_copy(update={"sha256": "a" * 64}),
    ):
        assert thumbnail_fingerprint(changed, settings, "ffmpeg A") != original
    assert (
        thumbnail_fingerprint(
            source, settings.model_copy(update={"quality": 3}), "ffmpeg A"
        )
        != original
    )
    assert thumbnail_fingerprint(source, settings, "ffmpeg B") != original
