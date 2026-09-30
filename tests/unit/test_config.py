from pathlib import Path

import pytest
import yaml

from swale_sounds.config import ConfigurationError, load_config


def test_explicit_configuration_resolves_paths_from_cwd(
    tmp_path, monkeypatch, config_data
):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    config = load_config(path)
    assert config.brand.name == "Swale Sounds"
    assert config.paths.data == tmp_path / "data"
    assert config.database.url == f"sqlite:///{tmp_path}/data/swale-sounds.db"
    assert not config.paths.data.exists()


def test_default_configuration_path(tmp_path, monkeypatch, config_data):
    monkeypatch.chdir(tmp_path)
    Path("config").mkdir()
    Path("config/swale-sounds.yaml").write_text(
        yaml.safe_dump(config_data), encoding="utf-8"
    )
    assert load_config().media.sample_rate == 48000


@pytest.mark.parametrize(
    "contents", ["brand: [", "", "{}", "- list", "!!python/object:bad {}"]
)
def test_malformed_or_missing_settings_fail_clearly(tmp_path, contents):
    path = tmp_path / "bad.yaml"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Cannot load configuration"):
        load_config(path)


def test_missing_file_fails_with_path(tmp_path):
    with pytest.raises(ConfigurationError, match=r"missing\.yaml"):
        load_config(tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("database", "url", "postgresql:///db"),
        ("database", "url", "sqlite:///db?uri=true"),
        ("media", "sample_rate", -1),
        ("media", "channels", 0),
        ("brand", "name", " "),
    ],
)
def test_invalid_values_fail_clearly(
    tmp_path, config_data, section, field, value
):
    config_data[section][field] = value
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=field):
        load_config(path)


def test_missing_database_url_fails_clearly(tmp_path, config_data):
    del config_data["database"]["url"]
    path = tmp_path / "missing-url.yaml"
    path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"database.url"):
        load_config(path)
