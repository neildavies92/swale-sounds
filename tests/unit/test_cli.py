from importlib.metadata import version

from typer.testing import CliRunner

from swale_sounds.cli import app


def test_help_succeeds_without_configuration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Swale Sounds" in result.output
    assert "version" in result.output


def test_version_matches_installed_package():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == version("swale-sounds")
