from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from swale_sounds.assets.probe import MediaInfo
from swale_sounds.models import RenderRun, RenderStage, RenderStatus
from swale_sounds.rendering.audio import AudioSettings
from swale_sounds.rendering.ffmpeg import RenderError
from swale_sounds.rendering.service import AudioIntent, input_fingerprint
from swale_sounds.rendering.video import (
    VideoSettings,
    parse_frame_rate,
    verify_video,
    video_arguments,
)
from swale_sounds.rendering.video_service import audio_is_current


@pytest.mark.parametrize(
    "raw,expected", [("30/1", 30), ("25/1", 25), ("30000/1001", 29.97002997)]
)
def test_frame_rates(raw, expected):
    assert parse_frame_rate(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw", [None, "invalid", "0/1", "1/0", "-30/1", "nan", "inf"]
)
def test_invalid_frame_rates(raw):
    with pytest.raises(RenderError):
        parse_frame_rate(raw)


@pytest.mark.parametrize(
    "values",
    [{"width": 161}, {"height": 91}, {"fps": 0}, {"fps": float("inf")}],
)
def test_invalid_video_settings(values):
    with pytest.raises(ValidationError):
        VideoSettings(**({"width": 160, "height": 90, "fps": 10} | values))


def test_video_command_semantics():
    art, audio, out = map(Path, ["cover ' ;.png", "audio file.m4a", "out.mp4"])
    settings = VideoSettings(width=320, height=180, fps=25)
    args = video_arguments(art, audio, out, settings, 60.02)
    for flag, value in {
        "-loop": "1",
        "-framerate": "25.0",
        "-r": "25.0",
        "-c:v": "libx264",
        "-preset": "medium",
        "-tune": "stillimage",
        "-pix_fmt": "yuv420p",
        "-c:a": "copy",
        "-t": "60.02",
        "-map_metadata": "-1",
        "-map_chapters": "-1",
        "-movflags": "+faststart",
        "-f": "mp4",
    }.items():
        assert args[args.index(flag) + 1] == value
    assert [args[i + 1] for i, v in enumerate(args) if v == "-map"] == [
        "0:v:0",
        "1:a:0",
    ]
    assert [args[i + 1] for i, v in enumerate(args) if v == "-i"] == [
        str(art),
        str(audio),
    ]
    assert args.index("-framerate") < args.index("-i")
    filters = args[args.index("-vf") + 1]
    assert "scale=320:180:force_original_aspect_ratio=decrease" in filters
    assert "pad=320:180:(ow-iw)/2:(oh-ih)/2:color=black" in filters
    assert "setsar=1" in filters and "-shortest" in args
    assert args[-1] == str(out)


@pytest.fixture
def intent():
    return AudioIntent(
        [],
        [],
        AudioSettings(
            target_duration_seconds=60,
            sample_rate=48000,
            channels=2,
            codec="aac",
            bitrate="192k",
            ambience_gain_db=-18,
        ),
        {
            "spec_sha256": "spec",
            "music": [{"asset_id": "a", "path": "a.wav", "sha256": "hash"}],
            "ambience": None,
        },
    )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "music",
        "hash",
        "ambience",
        "spec",
        "config",
        "renderer",
        "failed",
        "stage",
    ],
)
def test_current_audio_matches_historical_ffmpeg(intent, change):
    run = RenderRun(
        stage=RenderStage.AUDIO,
        status=RenderStatus.SUCCEEDED,
        renderer_version=1,
        ffmpeg_version="ffmpeg old build",
        input_fingerprint=input_fingerprint(
            intent.inputs,
            intent.settings.model_dump(mode="json"),
            "ffmpeg old build",
        ),
    )
    if change == "music":
        intent.inputs["music"].append({"asset_id": "b"})
    elif change == "hash":
        intent.inputs["music"][0]["sha256"] = "changed"
    elif change == "ambience":
        intent.inputs["ambience"] = {"asset_id": "rain"}
    elif change == "spec":
        intent.inputs["spec_sha256"] = "changed"
    elif change == "config":
        intent = AudioIntent(
            [],
            [],
            intent.settings.model_copy(update={"bitrate": "96k"}),
            intent.inputs,
        )
    elif change == "renderer":
        run.renderer_version = 0
    elif change == "failed":
        run.status = RenderStatus.FAILED
    elif change == "stage":
        run.stage = RenderStage.VIDEO
    assert audio_is_current(run, intent) is (change is None)


@pytest.mark.parametrize(
    "change",
    [
        "artwork",
        "audio_hash",
        "audio_id",
        "width",
        "height",
        "fps",
        "renderer",
        "ffmpeg",
    ],
)
def test_video_fingerprint_changes(change):
    inputs = {
        "spec_sha256": "spec",
        "artwork": {"sha256": "art"},
        "audio_render": {"render_id": "render-1", "sha256": "audio"},
    }
    config = VideoSettings(width=160, height=90, fps=10).model_dump(
        mode="json"
    )
    original = input_fingerprint(inputs, config, "ffmpeg", 1)
    assert original == input_fingerprint(
        deepcopy(inputs), deepcopy(config), "ffmpeg", 1
    )
    renderer, version = 1, "ffmpeg"
    if change == "artwork":
        inputs["artwork"]["sha256"] = "changed"
    elif change == "audio_hash":
        inputs["audio_render"]["sha256"] = "changed"
    elif change == "audio_id":
        inputs["audio_render"]["render_id"] = "render-2"
    elif change == "renderer":
        renderer = 2
    elif change == "ffmpeg":
        version = "new ffmpeg"
    else:
        config[change] += 2
    assert original != input_fingerprint(inputs, config, version, renderer)


@pytest.mark.parametrize(
    "change",
    [
        None,
        "codec_name",
        "width",
        "height",
        "pix_fmt",
        "avg_frame_rate",
        "audio_codec",
        "sample_rate",
        "channels",
        "duration",
        "video_duration",
        "audio_duration",
        "container",
        "extra",
        "missing",
    ],
)
def test_video_verification_checks_actual_streams(monkeypatch, intent, change):
    video = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 160,
        "height": 90,
        "pix_fmt": "yuv420p",
        "avg_frame_rate": "10/1",
        "duration": 60,
    }
    audio = {
        "codec_type": "audio",
        "codec_name": "aac",
        "sample_rate": 48000,
        "channels": 2,
        "duration": 60,
    }
    data = {
        "streams": [video, audio],
        "format": {"format_name": "mov,mp4", "duration": 60},
    }
    if change in {"codec_name", "pix_fmt", "avg_frame_rate"}:
        video[change] = "wrong"
    elif change in {"width", "height"}:
        video[change] = 2
    elif change == "audio_codec":
        audio["codec_name"] = "flac"
    elif change in {"sample_rate", "channels"}:
        audio[change] = 1
    elif change == "duration":
        data["format"]["duration"] = 58
    elif change == "video_duration":
        video["duration"] = 58
    elif change == "audio_duration":
        audio["duration"] = 58
    elif change == "container":
        data["format"]["format_name"] = "matroska"
    elif change == "extra":
        data["streams"].append({"codec_type": "subtitle"})
    elif change == "missing":
        data["streams"].pop()
    monkeypatch.setattr(
        "swale_sounds.rendering.video.probe_media",
        lambda _: MediaInfo.model_validate(data),
    )
    args = (
        Path("video.mp4"),
        VideoSettings(width=160, height=90, fps=10),
        intent.settings,
        60,
    )
    if change is None:
        assert verify_video(*args)[2] == 60
    else:
        with pytest.raises(RenderError):
            verify_video(*args)
