"""Render commands with thin argument and result presentation."""

from pathlib import Path
from typing import Annotated

import typer

from swale_sounds.config import DEFAULT_CONFIG_PATH, load_config
from swale_sounds.database import create_database_engine
from swale_sounds.rendering.ffmpeg import RenderError
from swale_sounds.rendering.service import render_audio
from swale_sounds.rendering.video_service import render_video
from swale_sounds.sessions.cli import command_errors

app = typer.Typer(
    help="Render derived media with recorded provenance.", no_args_is_help=True
)


@app.command("audio")
def audio_command(
    public_id: str,
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
    force: Annotated[
        bool, typer.Option("--force", help="Create a new immutable output.")
    ] = False,
) -> None:
    """Render continuous AAC audio or reuse an intact matching result."""
    with command_errors():
        settings = load_config(config)
        engine = create_database_engine(settings.database)
        try:
            result = render_audio(engine, settings, public_id, force=force)
            run = result.run
            typer.echo(
                f"Reusing {run.public_id}"
                if result.reused
                else f"Rendered audio for {public_id}"
            )
            typer.echo(f"Render: {run.public_id}")
            typer.echo(
                f"Output: {settings.paths.data / (run.output_path or '')}"
            )
            typer.echo(f"Duration: {run.duration_seconds:.2f}s")
            typer.echo(f"SHA-256: {run.output_sha256}")
        except RenderError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        finally:
            engine.dispose()


@app.command("video")
def video_command(
    public_id: str,
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
    force: Annotated[
        bool, typer.Option("--force", help="Create a new immutable output.")
    ] = False,
) -> None:
    """Render static artwork and current audio to H.264 MP4."""
    with command_errors():
        settings = load_config(config)
        engine = create_database_engine(settings.database)
        try:
            result = render_video(engine, settings, public_id, force=force)
            run = result.run
            typer.echo(
                f"Reusing {run.public_id}"
                if result.reused
                else f"Rendered video for {public_id}"
            )
            typer.echo(f"Render: {run.public_id}")
            typer.echo(
                f"Output: {settings.paths.data / (run.output_path or '')}"
            )
            typer.echo(f"Duration: {run.duration_seconds:.2f}s")
            typer.echo(f"SHA-256: {run.output_sha256}")
        except RenderError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        finally:
            engine.dispose()
