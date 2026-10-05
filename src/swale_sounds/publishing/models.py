"""Versioned, provider-independent publication package schema."""

import json
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from swale_sounds.sessions.schema import SessionSpec

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PublicationError(ValueError):
    """Planning cannot safely produce a reviewable package."""


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class MediaReference(PlanModel):
    # Paths are relative to the Session workspace, not the package directory.
    path: str
    sha256: Digest

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or "\\" in value
            or ":" in value
        ):
            raise ValueError("media references must be safe relative paths")
        return value


class VideoReference(MediaReference):
    render_id: str


class SourceReference(MediaReference):
    asset_id: str


class MusicProvenance(PlanModel):
    asset_id: str
    original_filename: str
    provider: str
    provider_model: str | None
    licence_notes: str | None
    licence_url: str | None
    licence_version: str | None
    attribution: Literal["required", "not_required", "unknown"]
    attribution_text: str | None


def tags_length(tags: list[str]) -> int:
    """YouTube counts separators and implied quotes around spaced tags."""
    return sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + max(
        0, len(tags) - 1
    )


class PublicationPlan(PlanModel):
    plan_version: Literal[1] = 1
    platform: Literal["youtube"] = "youtube"
    session_id: str
    spec_sha256: Digest
    path_base: Literal["session_workspace"] = "session_workspace"
    audio_render_id: str
    video: VideoReference
    source_assets: list[SourceReference]
    artwork: SourceReference
    title: str = Field(min_length=1, max_length=100)
    description: str
    tags: list[str]
    playlist_intents: list[str] = Field(default_factory=list)
    content: SessionSpec
    music: list[MusicProvenance]

    @field_validator("title", "description")
    @classmethod
    def youtube_text(cls, value: str) -> str:
        if "<" in value or ">" in value:
            raise ValueError("YouTube text cannot contain angle brackets")
        return value

    @field_validator("description")
    @classmethod
    def description_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 5000:
            raise ValueError("description exceeds YouTube's 5000-byte limit")
        return value

    @field_validator("tags")
    @classmethod
    def tag_limits(cls, value: list[str]) -> list[str]:
        if (
            tags_length(value) > 500
            or any(not tag.strip() for tag in value)
            or len(set(value)) != len(value)
        ):
            raise ValueError("tags must be unique and fit 500 characters")
        return value


def plan_bytes(plan: PublicationPlan) -> bytes:
    """Stable UTF-8 JSON, sorted keys, two-space indentation, final LF."""
    return (
        json.dumps(
            plan.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
