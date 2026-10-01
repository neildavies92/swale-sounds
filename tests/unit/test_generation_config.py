import pytest
import yaml
from pydantic import ValidationError

from swale_sounds.config import ArtworkGenerationConfig, load_config


def test_generation_defaults_and_phase1_compatibility(config_data, tmp_path):
    config_data.pop("generation", None)
    config = tmp_path / "phase1.yaml"
    config.write_text(yaml.safe_dump(config_data))
    settings = load_config(config).generation.artwork
    assert settings == ArtworkGenerationConfig()
    assert settings.model == "gpt-image-2.5-flare-2026-09-08"
    assert settings.size == "1536x864" and settings.timeout_seconds == 180
    assert settings.output_format == "png" and settings.provider == "openai"


@pytest.mark.parametrize(
    "values",
    [
        {"provider": "unknown"},
        {"model": " "},
        {"size": "auto"},
        {"size": "0x864"},
        {"size": "-1x100"},
        {"size": "9000x100"},
        {"size": "1536X864"},
        {"size": "abc"},
        {"quality": "invalid"},
        {"background": "invalid"},
        {"timeout_seconds": -1},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": "invalid"},
        {"output_format": "gif"},
        {"moderation": "off"},
        {"background": "transparent", "output_format": "jpeg"},
        {"unexpected": True},
    ],
)
def test_invalid_artwork_settings(values):
    with pytest.raises(ValidationError):
        ArtworkGenerationConfig.model_validate(values)


def test_unknown_provider_error_is_actionable():
    with pytest.raises(ValidationError, match="Unsupported artwork provider"):
        ArtworkGenerationConfig(provider="unknown")


def test_model_is_configurable_and_settings_are_frozen():
    settings = ArtworkGenerationConfig(model="future-snapshot", quality="low")
    assert settings.model == "future-snapshot"
    with pytest.raises(ValidationError):
        settings.model = "changed"
