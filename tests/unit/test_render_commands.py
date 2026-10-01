from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from swale_sounds.rendering.audio import (
    AudioSettings,
    encoding_arguments,
    preparation_arguments,
)
from swale_sounds.rendering.ffmpeg import RenderError, find_ffmpeg, run_ffmpeg
from swale_sounds.rendering.service import input_fingerprint


@pytest.mark.parametrize(
    "problem", ["codec", "rate", "channels", "duration", "container", "empty"]
)
def test_output_verification_rejects_wrong_media(
    fingerprint_data, monkeypatch, problem
):
    from swale_sounds.assets.probe import MediaInfo
    from swale_sounds.rendering.audio import verify_output

    stream = {
        "codec_type": "audio",
        "codec_name": "aac",
        "sample_rate": 48000,
        "channels": 2,
        "duration": 60,
    }
    container = "mov,mp4,m4a"
    if problem == "codec":
        stream["codec_name"] = "flac"
    elif problem == "rate":
        stream["sample_rate"] = 32000
    elif problem == "channels":
        stream["channels"] = 1
    elif problem == "duration":
        stream["duration"] = 58
    elif problem == "empty":
        stream["duration"] = 0
    else:
        container = "aac"
    media = MediaInfo.model_validate(
        {"streams": [stream], "format": {"format_name": container}}
    )
    monkeypatch.setattr(
        "swale_sounds.rendering.audio.probe_media", lambda _: media
    )
    with pytest.raises(RenderError):
        verify_output(
            Path("result.m4a"),
            AudioSettings.model_validate(fingerprint_data[1]),
        )


@pytest.fixture
def fingerprint_data():
    inputs = {
        "spec_sha256": "s" * 64,
        "music": [
            {"asset_id": "asset-a", "path": "01.wav", "sha256": "a" * 64},
            {"asset_id": "asset-b", "path": "02.wav", "sha256": "b" * 64},
        ],
        "ambience": None,
    }
    config = {
        "target_duration_seconds": 60,
        "sample_rate": 48000,
        "channels": 2,
        "codec": "aac",
        "bitrate": "192k",
        "ambience_gain_db": -18,
    }
    return inputs, config


def test_fingerprint_is_stable_across_dictionary_order(fingerprint_data):
    inputs, config = fingerprint_data
    assert input_fingerprint(inputs, config, "ffmpeg v1") == input_fingerprint(
        dict(reversed(list(inputs.items()))),
        dict(reversed(list(config.items()))),
        "ffmpeg v1",
    )


@pytest.mark.parametrize(
    "changed",
    [
        "order",
        "hash",
        "path",
        "ambience",
        "spec",
        "renderer",
        "ffmpeg",
        "target_duration_seconds",
        "sample_rate",
        "channels",
        "codec",
        "bitrate",
        "ambience_gain_db",
    ],
)
def test_fingerprint_changes_for_every_render_input(fingerprint_data, changed):
    inputs, config = fingerprint_data
    original = input_fingerprint(inputs, config, "ffmpeg v1")
    inputs, config = deepcopy(inputs), deepcopy(config)
    version, renderer = "ffmpeg v1", 1
    if changed == "order":
        inputs["music"].reverse()
    elif changed == "hash":
        inputs["music"][0]["sha256"] = "changed"
    elif changed == "path":
        inputs["music"][0]["path"] = "changed.wav"
    elif changed == "ambience":
        inputs["ambience"] = {"path": "rain.wav", "sha256": "c" * 64}
    elif changed == "spec":
        inputs["spec_sha256"] = "changed"
    elif changed == "renderer":
        renderer = 2
    elif changed == "ffmpeg":
        version = "ffmpeg v2"
    else:
        config[changed] = "different"
    assert input_fingerprint(inputs, config, version, renderer) != original


def test_audio_commands_use_configuration_and_explicit_streams(
    fingerprint_data,
):
    _, config = fingerprint_data
    config.update(
        sample_rate=32000, channels=1, bitrate="96k", ambience_gain_db=-25
    )
    settings = AudioSettings.model_validate(config)
    source = Path("source with 'quotes';.wav")
    prepare = preparation_arguments(source, Path("0001.flac"), settings)
    assert prepare[prepare.index("-i") + 1] == str(source)
    assert prepare[prepare.index("-map") + 1] == "0:a:0"
    assert prepare[prepare.index("-c:a") + 1] == "flac"
    assert prepare[prepare.index("-ar") + 1] == "32000"
    assert prepare[prepare.index("-ac") + 1] == "1"
    encode = encoding_arguments(
        Path("sequence.flac"), Path("rain.flac"), Path("out.m4a"), settings
    )
    assert encode[encode.index("-c:a") + 1] == "aac"
    assert encode[encode.index("-b:a") + 1] == "96k"
    assert encode[encode.index("-t") + 1] == "60"
    assert "volume=-25.0dB" in encode[encode.index("-filter_complex") + 1]
    assert encode.count("-stream_loop") == 2
    assert encode[encode.index("-map_metadata") + 1] == "-1"


def test_ffmpeg_runner_streams_logs_without_shell_or_timeout(
    tmp_path, monkeypatch
):
    def run(arguments, **kwargs):
        assert "-nostdin" in arguments and "-n" in arguments
        assert not kwargs.get("shell", False)
        assert "timeout" not in kwargs
        assert "capture_output" not in kwargs
        assert kwargs["stdout"] is kwargs["stderr"]
        kwargs["stderr"].write("test diagnostics\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("swale_sounds.rendering.ffmpeg.subprocess.run", run)
    log = tmp_path / "render.log"
    with log.open("w", encoding="utf-8") as stream:
        run_ffmpeg("/bin/ffmpeg", ["-i", "a; b.wav", "out.flac"], stream)
    contents = log.read_text()
    assert "a; b.wav" in contents
    assert "test diagnostics" in contents


@pytest.mark.parametrize("failure", ["exit", "os"])
def test_ffmpeg_runner_reports_failures(tmp_path, monkeypatch, failure):
    def run(*args, **kwargs):
        if failure == "os":
            raise OSError("cannot execute")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("swale_sounds.rendering.ffmpeg.subprocess.run", run)
    with (tmp_path / "log").open("w") as log, pytest.raises(RenderError):
        run_ffmpeg("ffmpeg", [], log)


@pytest.mark.parametrize("missing", ["ffmpeg", "ffprobe"])
def test_missing_executable_has_install_guidance(monkeypatch, missing):
    monkeypatch.setattr(
        "swale_sounds.rendering.ffmpeg.shutil.which",
        lambda name: None if name == missing else "/bin/ffmpeg",
    )
    with pytest.raises(RenderError, match=f"{missing} was not found"):
        find_ffmpeg()
