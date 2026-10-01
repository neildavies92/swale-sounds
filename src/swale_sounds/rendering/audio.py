"""FLAC preparation, concatenation, duration and AAC encoding stages."""

from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, ConfigDict, Field

from swale_sounds.assets.probe import MediaInfo, MediaStream, probe_media
from swale_sounds.rendering.ffmpeg import RenderError, run_ffmpeg

AUDIO_RENDERER_VERSION = 1
DURATION_TOLERANCE_SECONDS = 0.25


class AudioSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    target_duration_seconds: int = Field(gt=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(ge=1, le=2)
    codec: str
    bitrate: str
    ambience_gain_db: float


def preparation_arguments(
    source: Path, output: Path, settings: AudioSettings
) -> list[str]:
    return [
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-ar",
        str(settings.sample_rate),
        "-ac",
        str(settings.channels),
        "-c:a",
        "flac",
        str(output),
    ]


def encoding_arguments(
    sequence: Path,
    ambience: Path | None,
    output: Path,
    settings: AudioSettings,
) -> list[str]:
    arguments = ["-stream_loop", "-1", "-i", str(sequence)]
    if ambience is not None:
        arguments += [
            "-stream_loop",
            "-1",
            "-i",
            str(ambience),
            "-filter_complex",
            f"[1:a:0]volume={settings.ambience_gain_db}dB[ambience];"
            "[0:a:0][ambience]amix=inputs=2:duration=first:normalize=0[mixed]",
            "-map",
            "[mixed]",
        ]
    else:
        arguments += ["-map", "0:a:0"]
    return [
        *arguments,
        "-t",
        str(settings.target_duration_seconds),
        "-vn",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-ar",
        str(settings.sample_rate),
        "-ac",
        str(settings.channels),
        "-c:a",
        settings.codec,
        "-b:a",
        settings.bitrate,
        "-movflags",
        "+faststart",
        "-f",
        "ipod",
        str(output),
    ]


def positive_audio(path: Path) -> tuple[MediaStream, float]:
    return audio_details(probe_media(path), path)


def audio_details(media: MediaInfo, path: Path) -> tuple[MediaStream, float]:
    streams = [item for item in media.streams if item.codec_type == "audio"]
    if len(streams) != 1 or len(media.streams) != 1:
        raise RenderError(f"Expected exactly one audio stream: {path.name}")
    stream = streams[0]
    duration = (
        stream.duration
        if stream.duration is not None
        else media.format.duration
    )
    if duration is None or duration <= 0:
        raise RenderError(f"Audio must have positive duration: {path.name}")
    return stream, duration


def verify_output(
    path: Path, settings: AudioSettings
) -> tuple[MediaStream, float]:
    media = probe_media(path)
    stream, duration = audio_details(media, path)
    if (
        "mp4" not in (media.format.format_name or "").split(",")
        or stream.codec_name != "aac"
        or stream.sample_rate != settings.sample_rate
        or stream.channels != settings.channels
        or abs(duration - settings.target_duration_seconds)
        > DURATION_TOLERANCE_SECONDS
    ):
        raise RenderError(
            "Rendered output does not match requested audio settings: "
            f"{path.name}"
        )
    return stream, duration


def render_pipeline(
    executable: str,
    music: list[Path],
    ambience: Path | None,
    directory: Path,
    settings: AudioSettings,
    log: TextIO,
) -> Path:
    music_directory = directory / "music"
    music_directory.mkdir()
    prepared: list[Path] = []
    for index, source in enumerate(music, 1):
        destination = music_directory / f"{index:04d}.flac"
        run_ffmpeg(
            executable,
            preparation_arguments(source, destination, settings),
            log,
        )
        positive_audio(destination)
        prepared.append(destination)
    playlist = directory / "tracks.ffconcat"
    # Only generated relative numeric filenames enter concat syntax.
    playlist.write_text(
        "ffconcat version 1.0\n"
        + "".join(f"file 'music/{path.name}'\n" for path in prepared),
        encoding="utf-8",
        newline="\n",
    )
    sequence = directory / "sequence.flac"
    run_ffmpeg(
        executable,
        [
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            str(playlist),
            "-map",
            "0:a:0",
            "-map_metadata",
            "-1",
            "-c:a",
            "copy",
            str(sequence),
        ],
        log,
    )
    positive_audio(sequence)
    prepared_ambience = None
    if ambience is not None:
        prepared_ambience = directory / "ambience.flac"
        run_ffmpeg(
            executable,
            preparation_arguments(ambience, prepared_ambience, settings),
            log,
        )
        positive_audio(prepared_ambience)
    output = directory / "audio.m4a"
    run_ffmpeg(
        executable,
        encoding_arguments(sequence, prepared_ambience, output, settings),
        log,
    )
    return output
