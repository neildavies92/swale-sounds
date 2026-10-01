"""Validated YAML settings with working-directory-relative paths."""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

DEFAULT_CONFIG_PATH = Path("config/swale-sounds.yaml")


class ConfigurationError(ValueError):
    """Configuration could not be read or validated."""


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BrandConfig(SettingsModel):
    name: str = Field(min_length=1)
    tagline: str = Field(min_length=1)


class PathsConfig(SettingsModel):
    data: Path


class DatabaseConfig(SettingsModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_sqlite_url(cls, value: str) -> str:
        try:
            url = make_url(value)
        except ArgumentError as exc:
            raise ValueError("must be a valid SQLite URL") from exc
        if url.drivername != "sqlite" or not url.database:
            raise ValueError("use sqlite:///path.db or sqlite:///:memory:")
        if url.host or url.username or url.password or url.port or url.query:
            raise ValueError(
                "SQLite URL must not include credentials or query options"
            )
        return value


class VideoConfig(SettingsModel):
    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)
    fps: float = Field(gt=0, allow_inf_nan=False)


class AudioConfig(SettingsModel):
    codec: str = Field(min_length=1)
    bitrate: str = Field(pattern=r"^[1-9][0-9]*k$")


class AmbienceConfig(SettingsModel):
    gain_db: float = Field(allow_inf_nan=False)


class MediaConfig(SettingsModel):
    sample_rate: int = Field(gt=0, strict=True)
    channels: int = Field(ge=1, le=2, strict=True)
    video: VideoConfig
    audio: AudioConfig
    ambience: AmbienceConfig


class ArtworkGenerationConfig(SettingsModel):
    model_config = ConfigDict(frozen=True)

    provider: Literal["openai"] = "openai"
    model: str = Field(default="gpt-image-2.5-flare-2026-09-08", min_length=1)
    size: str = Field(
        default="1536x864", pattern=r"^[1-9][0-9]{0,4}x[1-9][0-9]{0,4}$"
    )
    quality: Literal["low", "medium", "high", "xhigh", "max", "auto"] = (
        "medium"
    )
    output_format: Literal["png", "jpeg", "webp"] = "png"
    background: Literal["opaque", "transparent", "auto"] = "opaque"
    moderation: Literal["auto", "low"] = "auto"
    timeout_seconds: float = Field(
        default=180, gt=0, le=3600, allow_inf_nan=False
    )

    @field_validator("provider", mode="before")
    @classmethod
    def supported_provider(cls, value: object) -> object:
        if value != "openai":
            raise ValueError(f"Unsupported artwork provider: {value}")
        return value

    @field_validator("size")
    @classmethod
    def sensible_dimensions(cls, value: str) -> str:
        if any(int(part) > 8192 for part in value.split("x")):
            raise ValueError("artwork dimensions must not exceed 8192 pixels")
        return value

    @model_validator(mode="after")
    def compatible_background(self) -> Self:
        if self.background == "transparent" and self.output_format == "jpeg":
            raise ValueError("transparent backgrounds require png or webp")
        return self


class GenerationConfig(SettingsModel):
    artwork: ArtworkGenerationConfig = Field(
        default_factory=ArtworkGenerationConfig
    )


class AppConfig(SettingsModel):
    brand: BrandConfig
    paths: PathsConfig
    database: DatabaseConfig
    media: MediaConfig
    generation: GenerationConfig = Field(default_factory=GenerationConfig)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load settings without creating directories or database tables."""
    try:
        contents = path.read_text(encoding="utf-8")
        config = AppConfig.model_validate(yaml.safe_load(contents))
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(
            f"Cannot load configuration '{path}': {exc}. "
            "Check that the file exists and contains valid settings."
        ) from exc
    config.paths.data = config.paths.data.expanduser().resolve()
    url = make_url(config.database.url)
    if url.database and url.database != ":memory:":
        url = url.set(database=str(Path(url.database).expanduser().resolve()))
    config.database.url = url.render_as_string(hide_password=False)
    return config
