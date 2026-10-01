"""Safe FFmpeg execution with file-backed diagnostics."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import TextIO


class RenderError(ValueError):
    """An expected prerequisite, integrity or rendering failure."""


def find_ffmpeg() -> tuple[str, str]:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RenderError(
            "ffmpeg was not found. Install FFmpeg "
            "and ensure ffmpeg is on PATH."
        )
    if shutil.which("ffprobe") is None:
        raise RenderError(
            "ffprobe was not found. Install FFmpeg "
            "and ensure ffprobe is on PATH."
        )
    try:
        result = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(f"Cannot determine FFmpeg version: {exc}") from exc
    lines = result.stdout.splitlines()
    if (
        result.returncode != 0
        or not lines
        or not lines[0].startswith("ffmpeg version")
    ):
        raise RenderError(
            "Cannot determine FFmpeg version; inspect its installation."
        )
    return executable, lines[0]


def run_ffmpeg(executable: str, arguments: list[str], log: TextIO) -> None:
    """Allow long-running media work and stream all diagnostics to disk."""
    command = [
        executable,
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-n",
        *arguments,
    ]
    log.write(json.dumps(command, ensure_ascii=False) + "\n")
    log.flush()
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            check=False,
        )
    except OSError as exc:
        raise RenderError(f"Cannot execute FFmpeg: {exc}") from exc
    if result.returncode != 0:
        raise RenderError(
            f"FFmpeg exited with code {result.returncode}; "
            "inspect the render log."
        )


def open_render_log(path: Path) -> TextIO:
    return path.open("x", encoding="utf-8", errors="replace", newline="\n")
