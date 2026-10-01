"""Safe input parsing and deterministic content-only specifications."""

import hashlib
import re
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class SessionSpecError(ValueError):
    """A source specification cannot be read or validated."""


def normalize_taxonomy(value: str) -> str:
    """Normalize separators and case without semantic substitutions."""
    value = re.sub(r"[\s_-]+", "_", value.strip().lower()).strip("_")
    if not value:
        raise ValueError("taxonomy value must not be empty")
    return value


type Taxonomy = Annotated[str, AfterValidator(normalize_taxonomy)]


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SessionInfo(SpecModel):
    title: str = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def meaningful_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must contain non-whitespace content")
        return value.strip()


class BpmRange(SpecModel):
    min: int = Field(gt=0, le=300)
    max: int = Field(gt=0, le=300)

    @model_validator(mode="after")
    def ordered_range(self) -> Self:
        if self.max < self.min:
            raise ValueError("max must be greater than or equal to min")
        return self


class MusicSpec(SpecModel):
    genre: list[Taxonomy] = Field(default_factory=list)
    mood: list[Taxonomy] = Field(default_factory=list)
    instruments: list[Taxonomy] = Field(default_factory=list)
    bpm: BpmRange | None = None


class ContextSpec(SpecModel):
    purpose: list[Taxonomy] = Field(default_factory=list)
    environment: list[Taxonomy] = Field(default_factory=list)
    location: list[Taxonomy] = Field(default_factory=list)
    weather: list[Taxonomy] = Field(default_factory=list)
    time: list[Taxonomy] = Field(default_factory=list)
    season: list[Taxonomy] = Field(default_factory=list)


class VisualSpec(SpecModel):
    style: Taxonomy | None = None
    animation: list[Taxonomy] = Field(default_factory=list)


class OutputSpec(SpecModel):
    duration_minutes: int = Field(gt=0, le=720)


class SessionSpec(SpecModel):
    spec_version: int
    session: SessionInfo
    music: MusicSpec
    context: ContextSpec
    visual: VisualSpec
    output: OutputSpec

    @field_validator("spec_version")
    @classmethod
    def supported_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError(f"unsupported spec_version {value}; expected 1")
        return value


def load_spec(path: Path) -> SessionSpec:
    """Read UTF-8 YAML and report source and field errors."""
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SessionSpecError(
            f"Cannot read specification '{path}': {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise SessionSpecError(
            f"Invalid specification '{path}': root must be a mapping"
        )
    try:
        return SessionSpec.model_validate(value)
    except ValidationError as exc:
        details = "\n".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False)
        )
        raise SessionSpecError(
            f"Invalid specification '{path}':\n{details}"
        ) from exc


def canonical_bytes(spec: SessionSpec) -> bytes:
    """Serialize sorted keys and explicit defaults, preserving list order."""
    return yaml.safe_dump(
        spec.model_dump(mode="json"),
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
        line_break="\n",
    ).encode("utf-8")


def sha256_bytes(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()
