from typer.testing import CliRunner

from swale_sounds.cli import app

runner = CliRunner()


def test_session_help():
    result = runner.invoke(app, ["session", "--help"])
    assert result.exit_code == 0
    for command in ("validate", "create", "list", "show"):
        assert command in result.output


def test_validate_has_no_side_effects_or_configuration_dependency(
    tmp_path, monkeypatch, spec_file
):
    monkeypatch.chdir(tmp_path)
    before = {
        path: path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    result = runner.invoke(app, ["session", "validate", str(spec_file)])
    assert result.exit_code == 0, result.output
    assert "Valid session specification" in result.output
    assert {
        path: path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before
    assert not (tmp_path / "data").exists()


def test_cli_complete_workflow(session_environment, spec_file):
    _, _, config = session_environment

    def invoke(*arguments):
        return runner.invoke(
            app, ["session", *arguments, "--config", str(config)]
        )

    empty = invoke("list")
    assert empty.exit_code == 0
    assert "No sessions found" in empty.output
    created = invoke("create", str(spec_file))
    assert created.exit_code == 0, created.output
    assert "Created session-000001" in created.output
    assert "Workspace:" in created.output
    listed = invoke("list")
    assert listed.exit_code == 0
    assert "session-000001" in listed.output
    assert "rainy paris cafe" in listed.output
    assert "created" in listed.output
    shown = invoke("show", "session-000001")
    assert shown.exit_code == 0, shown.output
    for expected in (
        "SHA-256:",
        "Created:",
        "Specification version: 1",
        "jazz",
        "duration_minutes: 90",
    ):
        assert expected in shown.output
    missing = invoke("show", "session-999999")
    assert missing.exit_code == 1
    assert "Session not found" in missing.output
    assert "Traceback" not in missing.output


def test_invalid_spec_cli_is_concise_and_leaves_no_workspace(
    session_environment, spec_file
):
    settings, _, config = session_environment
    spec_file.write_text("spec_version: 2\nsession: {}", encoding="utf-8")
    for arguments in (
        ["validate", str(spec_file)],
        ["create", str(spec_file), "--config", str(config)],
    ):
        result = runner.invoke(app, ["session", *arguments])
        assert result.exit_code == 1
        assert "spec_version" in result.output
        assert "session.title" in result.output
        assert "Traceback" not in result.output
    assert not settings.paths.data.exists()


def test_missing_schema_error_has_corrective_guidance(tmp_path, config_data):
    import yaml

    config_data["database"]["url"] = f"sqlite:///{tmp_path}/unmigrated.db"
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    result = runner.invoke(app, ["session", "list", "--config", str(config)])
    assert result.exit_code == 1
    assert "alembic upgrade head" in result.output
    assert "Traceback" not in result.output
