"""Exercise the installed Phase 1 CLI in a disposable, isolated workspace."""

import hashlib
import json
import shlex
import shutil
import sqlite3
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

import yaml

from swale_sounds.publishing.models import PublicationPlanV2, parse_plan
from swale_sounds.publishing.thumbnail import verify_thumbnail
from swale_sounds.rendering.ffmpeg import find_ffmpeg

REPOSITORY = Path(__file__).resolve().parents[1]
SESSION_ID = "session-000001"


class SmokeError(RuntimeError):
    """An acceptance check or subprocess failed."""


def run(command: list[str], root: Path) -> str:
    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise SmokeError(
            f"Command: {shlex.join(command)}\nExit code: {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def write_inputs(root: Path) -> tuple[Path, Path]:
    config = root / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "brand": {
                    "name": "Swale Sounds Smoke",
                    "tagline": "Phase 1 acceptance",
                },
                "paths": {"data": str(root / "data")},
                "database": {"url": f"sqlite:///{root / 'state.db'}"},
                "media": {
                    "sample_rate": 48000,
                    "channels": 2,
                    "video": {"width": 640, "height": 360, "fps": 10},
                    "audio": {"codec": "aac", "bitrate": "128k"},
                    "ambience": {"gain_db": -18},
                },
            }
        ),
        encoding="utf-8",
    )
    spec = root / "session.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "session": {"title": " Phase 1 smoke test "},
                "music": {"genre": ["Ambient"], "mood": ["Quiet Focus"]},
                "context": {
                    "purpose": ["Developer Testing"],
                    "weather": ["Rain"],
                },
                "visual": {"style": "Flat Colour", "animation": []},
                "output": {"duration_minutes": 1},
            }
        ),
        encoding="utf-8",
    )
    return config, spec


def verify_result(root: Path, ffprobe: str) -> None:
    # The CLI has no machine-readable status/output mode. Read only this DB.
    with sqlite3.connect(
        (root / "state.db").as_uri() + "?mode=ro", uri=True
    ) as db:
        state = db.execute("SELECT public_id, status FROM sessions").fetchall()
        rows = db.execute(
            "SELECT output_path, output_sha256 FROM render_runs "
            "WHERE stage = 'video' AND status = 'succeeded'"
        ).fetchall()
        publications = db.execute(
            "SELECT p.external_id, p.canonical_url, p.plan_sha256, "
            "p.plan_text, s.public_id, r.output_sha256 FROM publications p "
            "JOIN sessions s ON s.id = p.session_id "
            "JOIN render_runs r ON r.id = p.video_render_id "
            "WHERE r.stage = 'video' AND r.status = 'succeeded'"
        ).fetchall()
    if state != [(SESSION_ID, "video_rendered")] or len(rows) != 1:
        raise SmokeError(f"Unexpected final database state: {state}, {rows}")
    directory = root / "data" / "sessions" / SESSION_ID / "output" / "video"
    outputs = list(directory.glob("*.mp4"))
    if len(outputs) != 1:
        raise SmokeError(f"Expected one final MP4 under {directory}")
    output = outputs[0]
    if (
        output.is_symlink()
        or not output.is_file()
        or output.stat().st_size <= 0
    ):
        raise SmokeError("Final MP4 is missing, empty or not a regular file")
    if rows[0][0] != output.relative_to(root / "data").as_posix():
        raise SmokeError("Final MP4 does not match the successful RenderRun")
    with output.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != rows[0][1]:
            raise SmokeError("Final MP4 SHA-256 does not match its RenderRun")
    workspace = root / "data" / "sessions" / SESSION_ID
    manifest = workspace / "output/publish/youtube.json"
    plan = parse_plan(manifest.read_bytes())
    if not isinstance(plan, PublicationPlanV2):
        raise SmokeError("Expected a v2 plan with a thumbnail")
    verify_thumbnail(workspace, plan.thumbnail, plan.artwork, find_ffmpeg()[1])
    if len(publications) != 1 or publications[0] != (
        "LocalTest01",
        "https://www.youtube.com/watch?v=LocalTest01",
        hashlib.sha256(manifest.read_bytes()).hexdigest(),
        manifest.read_text(encoding="utf-8"),
        SESSION_ID,
        rows[0][1],
    ):
        raise SmokeError("Synthetic Publication does not match the exact plan")
    if (
        plan.session_id != SESSION_ID
        or workspace / plan.video.path != output
        or plan.video.sha256 != rows[0][1]
        or plan.content.output.duration_minutes != 1
    ):
        raise SmokeError("Publication plan does not match the produced video")
    media = json.loads(
        run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output),
            ],
            root,
        )
    )
    streams = media.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if len(streams) != 2 or len(videos) != 1 or len(audios) != 1:
        raise SmokeError("Expected exactly one video and one audio stream")
    video, audio = videos[0], audios[0]
    container = media.get("format", {})
    if not (
        "mp4" in container.get("format_name", "").split(",")
        and video.get("codec_name") == "h264"
        and video.get("width") == 640
        and video.get("height") == 360
        and audio.get("codec_name") == "aac"
        and int(audio.get("sample_rate", 0)) == 48000
        and audio.get("channels") == 2
        and abs(float(container.get("duration", 0)) - 60) <= 0.25
    ):
        raise SmokeError(f"Unexpected MP4 metadata: {media}")


def main() -> int:
    root: Path | None = None
    step = "checking prerequisites"
    try:
        print(f"[smoke] {step}", flush=True)
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            raise SmokeError(
                "Phase 1 smoke test requires ffmpeg and ffprobe on PATH."
            )
        scripts = Path(sysconfig.get_path("scripts"))
        cli, alembic = scripts / "swale-sounds", scripts / "alembic"
        if not cli.is_file() or not alembic.is_file():
            raise SmokeError(
                "Run make setup, then make smoke "
                "using the project environment."
            )
        step = "creating isolated environment"
        print(f"[smoke] {step}", flush=True)
        # mkdtemp allows failures (including interrupts) to retain render logs.
        root = Path(tempfile.mkdtemp(prefix="swale-phase1-smoke-")).resolve()
        config, spec = write_inputs(root)
        music, artwork = root / "input/music", root / "input/artwork"
        music.mkdir(parents=True)
        artwork.mkdir(parents=True)
        step = "generating source fixtures"
        print(f"[smoke] {step}", flush=True)
        run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-n",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3",
                str(music / "tone.wav"),
            ],
            root,
        )
        run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-n",
                "-f",
                "lavfi",
                "-i",
                "color=c=navy:s=96x128",
                "-frames:v",
                "1",
                str(artwork / "cover.png"),
            ],
            root,
        )
        steps = [
            (
                "migrating database",
                [
                    str(alembic),
                    "-c",
                    str(REPOSITORY / "alembic.ini"),
                    "-x",
                    f"config={config}",
                    "upgrade",
                    "head",
                ],
            ),
            (
                "validating session",
                [str(cli), "session", "validate", str(spec)],
            ),
            (
                "creating session",
                [
                    str(cli),
                    "session",
                    "create",
                    str(spec),
                    "--config",
                    str(config),
                ],
            ),
            (
                "importing music",
                [
                    str(cli),
                    "asset",
                    "import",
                    SESSION_ID,
                    str(music),
                    "--kind",
                    "music",
                    "--config",
                    str(config),
                ],
            ),
            (
                "importing artwork",
                [
                    str(cli),
                    "asset",
                    "import",
                    SESSION_ID,
                    str(artwork),
                    "--kind",
                    "artwork",
                    "--config",
                    str(config),
                ],
            ),
            (
                "rendering audio",
                [
                    str(cli),
                    "render",
                    "audio",
                    SESSION_ID,
                    "--config",
                    str(config),
                ],
            ),
            (
                "rendering video",
                [
                    str(cli),
                    "render",
                    "video",
                    SESSION_ID,
                    "--config",
                    str(config),
                ],
            ),
            (
                "planning publication",
                [
                    str(cli),
                    "publish",
                    "plan",
                    SESSION_ID,
                    "--config",
                    str(config),
                ],
            ),
            (
                "recording synthetic manual publication",
                [
                    str(cli),
                    "publish",
                    "record",
                    SESSION_ID,
                    "--video-id",
                    "LocalTest01",
                    "--config",
                    str(config),
                ],
            ),
            (
                "inspecting session",
                [
                    str(cli),
                    "session",
                    "show",
                    SESSION_ID,
                    "--config",
                    str(config),
                ],
            ),
        ]
        for step, command in steps:
            print(f"[smoke] {step}", flush=True)
            run(command, root)
        step = "verifying MP4"
        print(f"[smoke] {step}", flush=True)
        verify_result(root, ffprobe)
        step = "cleaning temporary environment"
        shutil.rmtree(root)
    except (
        SmokeError,
        OSError,
        ValueError,
        sqlite3.Error,
        KeyboardInterrupt,
    ) as exc:
        print(f"[smoke] FAILED during {step}: {exc}", file=sys.stderr)
        if root is not None:
            print(
                f"[smoke] Temporary environment retained: {root}",
                file=sys.stderr,
            )
        return 1
    print("Phase 1 smoke test PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
