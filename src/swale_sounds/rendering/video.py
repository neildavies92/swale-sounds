"""Static artwork, copied AAC and verified H.264 MP4 output."""

from fractions import Fraction
from math import isclose, isfinite
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, ConfigDict, Field, field_validator

from swale_sounds.assets.probe import MediaStream, probe_media
from swale_sounds.rendering.audio import (
    DURATION_TOLERANCE_SECONDS,
    AudioSettings,
)
from swale_sounds.rendering.ffmpeg import RenderError, run_ffmpeg

VIDEO_RENDERER_VERSION = 1


class VideoSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    encoder: str = "libx264"
    expected_codec: str = "h264"
    pixel_format: str = "yuv420p"
    preset: str = "medium"
    tune: str = "stillimage"
    audio_mode: str = "copy"
    container: str = "mp4"

    @field_validator("width", "height")
    @classmethod
    def even_dimension(cls, value: int) -> int:
        if value % 2:
            raise ValueError("yuv420p requires even video dimensions")
        return value


def parse_frame_rate(value: str | None) -> float:
    try:
        rate = float(Fraction(value or ""))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise RenderError(f"Invalid video frame rate: {value}") from exc
    if not isfinite(rate) or rate <= 0:
        raise RenderError(f"Invalid video frame rate: {value}")
    return rate


def video_arguments(
    artwork: Path,
    audio: Path,
    output: Path,
    settings: VideoSettings,
    duration: float,
) -> list[str]:
    return [
        "-loop",
        "1",
        "-framerate",
        str(settings.fps),
        "-i",
        str(artwork),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        f"scale={settings.width}:{settings.height}:"
        "force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
        "-c:v",
        settings.encoder,
        "-preset",
        settings.preset,
        "-tune",
        settings.tune,
        "-pix_fmt",
        settings.pixel_format,
        "-r",
        str(settings.fps),
        "-c:a",
        settings.audio_mode,
        "-shortest",
        "-t",
        str(duration),
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-movflags",
        "+faststart",
        "-f",
        settings.container,
        str(output),
    ]


def verify_video(
    path: Path,
    settings: VideoSettings,
    audio_settings: AudioSettings,
    audio_duration: float,
) -> tuple[MediaStream, MediaStream, float]:
    media = probe_media(path)
    videos = [s for s in media.streams if s.codec_type == "video"]
    audios = [s for s in media.streams if s.codec_type == "audio"]
    if len(videos) != 1 or len(audios) != 1 or len(media.streams) != 2:
        raise RenderError("Expected exactly one video and one audio stream")
    video, audio = videos[0], audios[0]
    duration = media.format.duration
    if (
        "mp4" not in (media.format.format_name or "").split(",")
        or video.codec_name != settings.expected_codec
        or video.width != settings.width
        or video.height != settings.height
        or video.pix_fmt != settings.pixel_format
        or not isclose(
            parse_frame_rate(video.avg_frame_rate),
            settings.fps,
            rel_tol=1e-5,
            abs_tol=1e-3,
        )
        or audio.codec_name != "aac"
        or audio.sample_rate != audio_settings.sample_rate
        or audio.channels != audio_settings.channels
        or duration is None
        or duration <= 0
        or abs(duration - audio_duration) > DURATION_TOLERANCE_SECONDS
        or any(
            s.duration is None
            or abs(s.duration - audio_duration) > DURATION_TOLERANCE_SECONDS
            for s in (video, audio)
        )
    ):
        raise RenderError(
            "Rendered video does not match requested media settings"
        )
    return video, audio, duration


def render_video_pipeline(
    executable: str,
    artwork: Path,
    audio: Path,
    directory: Path,
    settings: VideoSettings,
    duration: float,
    log: TextIO,
) -> Path:
    output = directory / "video.mp4"
    run_ffmpeg(
        executable,
        video_arguments(artwork, audio, output, settings, duration),
        log,
    )
    return output
