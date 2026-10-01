import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from swale_sounds.assets.probe import (
    MediaProbeError,
    parse_probe_json,
    probe_media,
)
from swale_sounds.assets.provenance import AssetError
from swale_sounds.assets.service import compatible_stream
from swale_sounds.models import AssetKind


def test_parse_audio_metadata_and_numeric_strings():
    info = parse_probe_json(
        json.dumps(
            {
                "streams": [
                    {
                        "codec_type": "audio",
                        "codec_name": "flac",
                        "sample_rate": "48000",
                        "channels": 2,
                    }
                ],
                "format": {"format_name": "flac", "duration": "183.52"},
            }
        )
    )
    assert info.streams[0].sample_rate == 48000
    assert info.streams[0].channels == 2
    assert info.format.duration == 183.52


def test_parse_still_image_metadata():
    info = parse_probe_json(
        json.dumps(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "png",
                        "width": 2,
                        "height": 3,
                        "nb_read_frames": "1",
                    }
                ],
                "format": {"format_name": "png_pipe"},
            }
        )
    )
    stream = compatible_stream(info, AssetKind.ARTWORK, Path("cover.png"))
    assert (stream.width, stream.height) == (2, 3)


def test_missing_optional_probe_values_are_none():
    info = parse_probe_json(
        '{"streams":[{"codec_type":"audio","duration":"N/A"}]}'
    )
    assert info.streams[0].duration is None
    assert info.streams[0].channels is None
    assert info.format.format_name is None


@pytest.mark.parametrize(
    "contents",
    [
        "not JSON",
        "[]",
        "null",
        "{}",
        '{"streams": []}',
        '{"streams": "audio"}',
        '{"streams": [{"codec_type": "audio", "sample_rate": "bad"}]}',
        '{"streams": [{"codec_type": "audio", "duration": "NaN"}]}',
    ],
)
def test_invalid_probe_json_is_rejected(contents):
    with pytest.raises(MediaProbeError, match="invalid media metadata"):
        parse_probe_json(contents)


@pytest.mark.parametrize("frames", [0, 2, None])
def test_artwork_requires_exactly_one_image_frame(frames):
    info = parse_probe_json(
        json.dumps(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "png",
                        "width": 1,
                        "height": 1,
                        "nb_read_frames": frames,
                    }
                ],
                "format": {"format_name": "png_pipe"},
            }
        )
    )
    with pytest.raises(AssetError, match="still image"):
        compatible_stream(info, AssetKind.ARTWORK, Path("cover.png"))


def test_renamed_video_and_image_as_audio_are_rejected():
    info = parse_probe_json(
        '{"streams":[{"codec_type":"video","codec_name":"h264","width":2,"height":2,"nb_read_frames":1}],"format":{"format_name":"mov,mp4"}}'
    )
    with pytest.raises(AssetError, match="still image"):
        compatible_stream(info, AssetKind.ARTWORK, Path("cover.png"))
    with pytest.raises(AssetError, match="No audio stream"):
        compatible_stream(info, AssetKind.MUSIC, Path("fake.mp3"))


def test_missing_ffprobe_has_install_guidance(monkeypatch):
    monkeypatch.setattr(
        "swale_sounds.assets.probe.shutil.which", lambda _: None
    )
    with pytest.raises(MediaProbeError, match="Install FFmpeg"):
        probe_media(Path("test.wav"))


def test_probe_uses_safe_arguments_and_parses_stdout(tmp_path, monkeypatch):
    path = tmp_path / "track; touch unwanted.wav"
    monkeypatch.setattr(
        "swale_sounds.assets.probe.shutil.which", lambda _: "/bin/ffprobe"
    )

    def run(command, **kwargs):
        assert command[-1] == str(path)
        assert command[0] == "/bin/ffprobe"
        assert not kwargs.get("shell", False)
        assert kwargs["timeout"] > 0
        return SimpleNamespace(
            returncode=0,
            stdout='{"streams":[{"codec_type":"audio"}]}',
            stderr="",
        )

    monkeypatch.setattr("swale_sounds.assets.probe.subprocess.run", run)
    assert probe_media(path).streams[0].codec_type == "audio"


@pytest.mark.parametrize("failure", ["exit", "timeout", "execution"])
def test_probe_operational_failures_are_reported(monkeypatch, failure):
    monkeypatch.setattr(
        "swale_sounds.assets.probe.shutil.which", lambda _: "/bin/ffprobe"
    )

    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 60)
        if failure == "execution":
            raise OSError("unavailable")
        return SimpleNamespace(returncode=1, stdout="", stderr="invalid media")

    monkeypatch.setattr("swale_sounds.assets.probe.subprocess.run", run)
    with pytest.raises(MediaProbeError):
        probe_media(Path("bad.wav"))
