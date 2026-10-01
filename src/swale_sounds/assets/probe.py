"""A typed ffprobe boundary independent of persistence and workspaces."""

import shutil
import subprocess
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)


class MediaProbeError(ValueError):
    """ffprobe is unavailable, failed, or returned invalid metadata."""


class MediaStream(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    codec_type: str
    codec_name: str | None = None
    duration: float | None = Field(default=None, ge=0)
    sample_rate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    nb_read_frames: int | None = Field(default=None, ge=0)

    @field_validator(
        "duration",
        "sample_rate",
        "channels",
        "width",
        "height",
        "nb_read_frames",
        mode="before",
    )
    @classmethod
    def unavailable(cls, value: object) -> object:
        return None if value == "N/A" else value


class MediaFormat(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    format_name: str | None = None
    duration: float | None = Field(default=None, ge=0)

    @field_validator("duration", mode="before")
    @classmethod
    def unavailable(cls, value: object) -> object:
        return None if value == "N/A" else value


class MediaInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    streams: list[MediaStream] = Field(min_length=1)
    format: MediaFormat = Field(default_factory=MediaFormat)


def parse_probe_json(contents: str) -> MediaInfo:
    try:
        return MediaInfo.model_validate_json(contents)
    except ValidationError as exc:
        raise MediaProbeError(
            "ffprobe returned invalid media metadata"
        ) from exc


def probe_media(path: Path, *, count_frames: bool = False) -> MediaInfo:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise MediaProbeError(
            "ffprobe was not found. Install FFmpeg "
            "and ensure ffprobe is on PATH."
        )
    command = [
        executable,
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
    ]
    if count_frames:
        command.append("-count_frames")
    command.append(str(path.resolve()))
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaProbeError(f"Cannot inspect '{path.name}': {exc}") from exc
    if result.returncode != 0:
        raise MediaProbeError(
            f"ffprobe could not inspect '{path.name}': "
            f"{result.stderr.strip()[:500]}"
        )
    return parse_probe_json(result.stdout)
