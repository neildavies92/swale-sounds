import json

import pytest
from typer.testing import CliRunner

from swale_sounds.cli import app

runner = CliRunner()


def invoke(environment, *arguments):
    return runner.invoke(
        app, ["asset", *arguments, "--config", str(environment[2])]
    )


def test_asset_help():
    result = runner.invoke(app, ["asset", "--help"])
    assert result.exit_code == 0
    for name in ("import", "list", "manifest"):
        assert name in result.output


def test_asset_cli_end_to_end_and_provenance(
    asset_session, media_files, tmp_path
):
    settings, _, _, session = asset_session
    empty = invoke(asset_session, "list", session.public_id)
    assert empty.exit_code == 0
    assert "No assets found" in empty.output
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Café jazz", encoding="utf-8")
    parameters = tmp_path / "parameters.json"
    parameters.write_text('{"seed":1}', encoding="utf-8")
    result = invoke(
        asset_session,
        "import",
        session.public_id,
        str(media_files[0]),
        "--kind",
        "music",
        "--provider",
        "example",
        "--provider-model",
        "v1",
        "--provider-plan",
        "test",
        "--licence-notes",
        "supplied",
        "--licence-url",
        "https://example.org/terms",
        "--licence-version",
        "v2",
        "--generation-prompt-file",
        str(prompt),
        "--generation-parameters-file",
        str(parameters),
    )
    assert result.exit_code == 0, result.output
    assert "Imported 1 music assets" in result.output
    duplicate = invoke(
        asset_session,
        "import",
        session.public_id,
        str(media_files[0]),
        "--kind",
        "music",
    )
    assert duplicate.exit_code == 0
    assert "Skipped duplicate: track.wav" in duplicate.output
    listed = invoke(asset_session, "list", session.public_id)
    assert listed.exit_code == 0
    assert "music_source" in listed.output
    assert "track.wav" in listed.output
    manifest = invoke(asset_session, "manifest", session.public_id)
    assert manifest.exit_code == 0, manifest.output
    path = (
        settings.paths.data / session.workspace_path / "manifests/assets.json"
    )
    record = json.loads(path.read_bytes())["assets"][0]
    assert record["generation_prompt"] == "Café jazz"
    assert record["generation_parameters"] == {"seed": 1}
    assert record["provider"] == "example"


@pytest.mark.parametrize("operation", ["import", "list", "manifest"])
def test_cli_unknown_session_is_concise(asset_session, media_files, operation):
    arguments = [operation, "session-999999"]
    if operation == "import":
        arguments += [str(media_files[0]), "--kind", "music"]
    result = invoke(asset_session, *arguments)
    assert result.exit_code == 1
    assert "Session not found" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "case", ["missing", "kind", "unsupported", "fake", "provenance"]
)
def test_cli_input_errors_leave_no_assets(
    asset_session, media_files, tmp_path, case
):
    path = media_files[0]
    kind = "music"
    extra = []
    if case == "missing":
        path = tmp_path / "missing.wav"
    elif case == "kind":
        kind = "video"
    elif case == "unsupported":
        path = tmp_path / "file.txt"
        path.write_text("text")
    elif case == "fake":
        path = tmp_path / "fake.mp3"
        path.write_text("text")
    else:
        extra = [
            "--generation-parameters-file",
            str(tmp_path / "missing.json"),
        ]
    result = invoke(
        asset_session,
        "import",
        asset_session[3].public_id,
        str(path),
        "--kind",
        kind,
        *extra,
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    listed = invoke(asset_session, "list", asset_session[3].public_id)
    assert "No assets found" in listed.output


def test_cli_missing_ffprobe_is_actionable(
    asset_session, media_files, monkeypatch
):
    monkeypatch.setattr(
        "swale_sounds.assets.probe.shutil.which", lambda _: None
    )
    result = invoke(
        asset_session,
        "import",
        asset_session[3].public_id,
        str(media_files[0]),
        "--kind",
        "music",
    )
    assert result.exit_code == 1
    assert "Install FFmpeg" in result.output
