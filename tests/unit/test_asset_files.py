import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from swale_sounds.assets.files import (
    calculate_sha256,
    copy_verified,
    discover_files,
    safe_child,
    safe_workspace,
)
from swale_sounds.assets.provenance import AssetError
from swale_sounds.models import AssetKind


@pytest.mark.parametrize("contents", [b"", b"media", b"a" * 3_000_000])
def test_hash_matches_standard_sha256(tmp_path, contents):
    path = tmp_path / "source.wav"
    path.write_bytes(contents)
    assert calculate_sha256(path) == hashlib.sha256(contents).hexdigest()
    duplicate = tmp_path / "copy.wav"
    duplicate.write_bytes(contents)
    assert calculate_sha256(path) == calculate_sha256(duplicate)
    duplicate.write_bytes(contents + b"changed")
    assert calculate_sha256(path) != calculate_sha256(duplicate)


def test_hash_uses_bounded_reads(monkeypatch):
    class BoundedReader(BytesIO):
        def read(self, size=-1):
            assert 0 < size < 3_000_000
            return super().read(size)

    contents = b"a" * 3_000_000
    monkeypatch.setattr(Path, "open", lambda *args: BoundedReader(contents))
    assert (
        calculate_sha256(Path("unused"))
        == hashlib.sha256(contents).hexdigest()
    )


def test_discovery_sorts_case_sensitively_and_is_nonrecursive(tmp_path):
    for name in ("z.wav", "A.wav", "b.wav"):
        (tmp_path / name).touch()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/ignored.txt").touch()
    assert [p.name for p in discover_files(tmp_path, AssetKind.MUSIC)] == [
        "A.wav",
        "b.wav",
        "z.wav",
    ]


@pytest.mark.parametrize(
    "case", ["missing", "empty", "unsupported", "symlink", "ancestor"]
)
def test_discovery_rejects_invalid_sources(tmp_path, case):
    source = tmp_path / "source"
    if case == "empty":
        source.mkdir()
    elif case == "unsupported":
        source = tmp_path / "track.txt"
        source.touch()
    elif case in {"symlink", "ancestor"}:
        real = tmp_path / "real"
        real.mkdir()
        (real / "track.wav").touch()
        source.symlink_to(real, target_is_directory=True)
        if case == "ancestor":
            source /= "track.wav"
    with pytest.raises(AssetError):
        discover_files(source, AssetKind.MUSIC)


def test_directory_symlink_is_rejected_without_recursing(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(AssetError, match="Symbolic links"):
        discover_files(tmp_path, AssetKind.MUSIC)


@pytest.mark.parametrize("relative", ["../escape", "/outside"])
def test_workspace_paths_cannot_escape_root(tmp_path, relative):
    with pytest.raises(AssetError):
        safe_workspace(tmp_path, relative)
    with pytest.raises(AssetError):
        safe_child(tmp_path, relative)


def test_copy_verifies_hash_and_does_not_modify_source(tmp_path):
    source = tmp_path / "original.wav"
    source.write_bytes(b"source media")
    destination = tmp_path / "imported.wav"
    with copy_verified(source, destination, calculate_sha256(source)):
        assert destination.read_bytes() == source.read_bytes()
    assert destination.read_bytes() == b"source media"
    assert source.read_bytes() == b"source media"


def test_copy_mismatch_removes_only_new_copy(tmp_path):
    source = tmp_path / "original.wav"
    source.write_bytes(b"changed after preflight")
    destination = tmp_path / "imported.wav"
    with (
        pytest.raises(AssetError, match="SHA-256"),
        copy_verified(source, destination, "wrong"),
    ):
        pytest.fail("mismatch accepted")
    assert not destination.exists()
    assert source.exists()


def test_copy_never_overwrites_existing_destination(tmp_path):
    source = tmp_path / "original.wav"
    source.write_bytes(b"new")
    destination = tmp_path / "imported.wav"
    destination.write_bytes(b"keep")
    with (
        pytest.raises(FileExistsError),
        copy_verified(source, destination, calculate_sha256(source)),
    ):
        pytest.fail("existing destination accepted")
    assert destination.read_bytes() == b"keep"
