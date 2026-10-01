"""Exclusive workspace creation with compensating cleanup."""

import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

WORKSPACE_DIRECTORIES = (
    "music/source",
    "music/processed",
    "artwork/source",
    "ambience/source",
    "manifests",
    "intermediate",
    "logs",
    "output",
)


class SessionWorkspaceError(OSError):
    """A workspace cannot be safely created or cleaned up."""


def workspace_path(data_root: Path, public_id: str) -> Path:
    if re.fullmatch(r"session-[0-9]{6,}", public_id) is None:
        raise SessionWorkspaceError(f"Invalid session ID: {public_id}")
    root = data_root.resolve()
    path = root / "sessions" / public_id
    if not path.resolve().is_relative_to(root):
        raise SessionWorkspaceError(f"Workspace escapes data root: {path}")
    return path


@contextmanager
def create_workspace(
    data_root: Path, public_id: str, contents: bytes
) -> Iterator[Path]:
    """Keep a new workspace only if the caller completes successfully."""
    path = workspace_path(data_root, public_id)
    created = False
    completed = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir(exist_ok=False)
        created = True
        for directory in WORKSPACE_DIRECTORIES:
            (path / directory).mkdir(parents=True)
        with (path / "session.yaml").open("xb") as stream:
            stream.write(contents)
        yield path
        completed = True
    except OSError as exc:
        raise SessionWorkspaceError(
            f"Cannot create workspace '{path}': {exc}. "
            "Check permissions and existing workspace contents."
        ) from exc
    finally:
        if created and not completed:
            try:
                shutil.rmtree(path)
            except OSError as exc:
                raise SessionWorkspaceError(
                    f"Failed to remove incomplete workspace '{path}'; "
                    "inspect and remove it manually before retrying."
                ) from exc
