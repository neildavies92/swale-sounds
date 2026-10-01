"""Bounded-memory hashing, deterministic discovery and exclusive copying."""

import hashlib
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from swale_sounds.assets.provenance import AssetError
from swale_sounds.models import AssetKind

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SOURCE_DIRECTORIES = {
    AssetKind.MUSIC: "music/source",
    AssetKind.ARTWORK: "artwork/source",
    AssetKind.AMBIENCE: "ambience/source",
}


def reject_symlinks(path: Path) -> None:
    """Reject both selected symlinks and unexpected symlink ancestors."""
    for component in (path, *path.parents):
        if component.is_symlink():
            raise AssetError(f"Symbolic links are not supported: {component}")


def discover_files(source: Path, kind: AssetKind) -> list[Path]:
    source = source.expanduser().absolute()
    reject_symlinks(source)
    if source.is_file():
        candidates = [source]
    elif source.is_dir():
        candidates = []
        for path in sorted(source.iterdir(), key=lambda item: item.name):
            reject_symlinks(path)
            if path.is_dir():
                continue
            if not path.is_file():
                raise AssetError(f"Source is not a regular file: {path}")
            candidates.append(path)
    else:
        raise AssetError(
            f"Source is not a readable file or directory: {source}"
        )
    if not candidates:
        raise AssetError(f"No source files found in: {source}")
    extensions = (
        IMAGE_EXTENSIONS if kind == AssetKind.ARTWORK else AUDIO_EXTENSIONS
    )
    for path in candidates:
        if path.suffix.lower() not in extensions:
            raise AssetError(f"Unsupported {kind.value} file: {path.name}")
    return candidates


def calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_workspace(data_root: Path, relative: str) -> Path:
    root = data_root.resolve()
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts or not part.parts:
        raise AssetError(f"Invalid persisted workspace path: {relative}")
    path = root / part
    reject_symlinks(path)
    if not path.resolve().is_relative_to(root) or not path.is_dir():
        raise AssetError(f"Session workspace is missing or unsafe: {path}")
    return path


def safe_child(workspace: Path, relative: str) -> Path:
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts:
        raise AssetError(f"Invalid asset path: {relative}")
    path = workspace / part
    reject_symlinks(path)
    if not path.resolve().is_relative_to(workspace.resolve()):
        raise AssetError(f"Asset path escapes workspace: {path}")
    return path


@contextmanager
def copy_verified(
    source: Path, destination: Path, expected_hash: str
) -> Iterator[None]:
    """Keep a new exclusive copy only when the enclosing import succeeds."""
    created = False
    completed = False
    try:
        reject_symlinks(source)
        reject_symlinks(destination)
        with source.open("rb") as src, destination.open("xb") as dst:
            created = True
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        if calculate_sha256(destination) != expected_hash:
            raise AssetError(
                f"Copied SHA-256 does not match source: {source.name}"
            )
        yield
        completed = True
    finally:
        if created and not completed:
            try:
                destination.unlink()
            except OSError as exc:
                raise AssetError(
                    f"Could not clean up failed import '{destination}'; "
                    "inspect it manually."
                ) from exc
