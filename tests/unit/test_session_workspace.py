from pathlib import Path

import pytest

from swale_sounds.sessions.workspace import (
    SessionWorkspaceError,
    create_workspace,
    workspace_path,
)


def test_workspace_layout_and_utf8(tmp_path):
    content = "title: café\n".encode()
    with create_workspace(tmp_path, "session-000001", content) as path:
        assert path.is_relative_to(tmp_path)
    assert (path / "session.yaml").read_bytes() == content
    directories = {
        p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_dir()
    }
    assert directories == {
        "music",
        "music/source",
        "music/processed",
        "artwork",
        "artwork/source",
        "ambience",
        "ambience/source",
        "manifests",
        "intermediate",
        "logs",
        "output",
    }


@pytest.mark.parametrize(
    "public_id",
    ["../escape", "/tmp/escape", "session-../", "session-000001/child"],
)
def test_invalid_id_cannot_escape_data_root(tmp_path, public_id):
    with pytest.raises(SessionWorkspaceError, match="Invalid session ID"):
        workspace_path(tmp_path, public_id)


def test_symlink_cannot_redirect_workspace_outside_data_root(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "sessions").symlink_to(outside, target_is_directory=True)
    with (
        pytest.raises(SessionWorkspaceError, match="escapes data root"),
        create_workspace(root, "session-000001", b"test"),
    ):
        pytest.fail("unsafe workspace was accepted")
    assert not list(outside.iterdir())


def test_existing_workspace_is_preserved(tmp_path):
    path = tmp_path / "sessions/session-000001"
    path.mkdir(parents=True)
    (path / "important.txt").write_text("keep")
    with (
        pytest.raises(SessionWorkspaceError, match="Cannot create workspace"),
        create_workspace(tmp_path, "session-000001", b"new"),
    ):
        pytest.fail("existing workspace was accepted")
    assert (path / "important.txt").read_text() == "keep"
    assert not (path / "session.yaml").exists()


def test_partial_directory_creation_is_cleaned_up(tmp_path, monkeypatch):
    original = Path.mkdir

    def fail_logs(self, *args, **kwargs):
        if self.name == "logs":
            raise PermissionError("injected filesystem failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_logs)
    with (
        pytest.raises(SessionWorkspaceError, match="injected filesystem"),
        create_workspace(tmp_path, "session-000001", b"test"),
    ):
        pytest.fail("failed creation returned")
    assert not (tmp_path / "sessions/session-000001").exists()


def test_spec_write_failure_removes_only_new_workspace(tmp_path, monkeypatch):
    original = Path.open

    def fail_write(self, *args, **kwargs):
        if self.name == "session.yaml":
            raise OSError("injected write failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_write)
    with (
        pytest.raises(SessionWorkspaceError, match="injected write"),
        create_workspace(tmp_path, "session-000001", b"test"),
    ):
        pytest.fail("failed write returned")
    assert not (tmp_path / "sessions/session-000001").exists()
