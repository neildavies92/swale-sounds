"""Local publication package commands."""

from pathlib import Path
from typing import Annotated

import typer

from swale_sounds.config import DEFAULT_CONFIG_PATH, load_config
from swale_sounds.database import create_database_engine
from swale_sounds.publishing.models import PublicationError
from swale_sounds.publishing.records import (
    list_publications,
    record_publication,
)
from swale_sounds.publishing.service import create_publication_plan
from swale_sounds.sessions.cli import command_errors

app = typer.Typer(
    help="Prepare local publication packages without uploading.",
    no_args_is_help=True,
)


@app.command("plan")
def plan_command(
    public_id: str,
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Plan a YouTube publication from a verified current video render."""
    with command_errors():
        settings = load_config(config)
        engine = create_database_engine(settings.database)
        try:
            plan, path = create_publication_plan(engine, settings, public_id)
            typer.echo(f"Publication package: {path.parent}")
            typer.echo(f"Manifest: {path}")
            typer.echo(f"Title: {plan.title}")
            typer.echo(
                f"Video: {plan.video.path} (Session workspace relative)"
            )
            typer.echo(f"Render: {plan.video.render_id}")
            typer.echo("Review locally; nothing has been uploaded.")
        except PublicationError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        finally:
            engine.dispose()


@app.command("record")
def record_command(
    public_id: str,
    video_id: Annotated[str, typer.Option("--video-id")],
    published_at: Annotated[str | None, typer.Option("--published-at")] = None,
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Record an operator-confirmed upload of the existing reviewed plan."""
    with command_errors():
        settings = load_config(config)
        engine = create_database_engine(settings.database)
        try:
            row, reused = record_publication(
                engine, settings, public_id, video_id, published_at
            )
            typer.echo(
                f"{'Existing' if reused else 'Recorded'} Publication: "
                f"{row.public_id}"
            )
            typer.echo(
                f"URL: {row.canonical_url}\n"
                f"Published: {row.published_at.isoformat()}"
            )
            typer.echo(f"Plan v{row.plan_version}: {row.plan_sha256}")
        except PublicationError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        finally:
            engine.dispose()


def display_records(config: Path, public_id: str | None = None) -> None:
    with command_errors():
        engine = create_database_engine(load_config(config).database)
        try:
            rows = list_publications(engine, public_id)
            if not rows:
                if public_id:
                    typer.echo(f"Publication not found: {public_id}", err=True)
                    raise typer.Exit(1)
                typer.echo("No Publications recorded.")
            for row in rows:
                typer.echo(
                    f"{row.public_id}\nSession: {row.session.public_id}\n"
                    f"Platform: {row.platform}\nVideo ID: {row.external_id}\n"
                    f"URL: {row.canonical_url}\n"
                    f"Published: {row.published_at.isoformat()}\n"
                    f"Render: {row.video_render.public_id}\n"
                    f"Plan v{row.plan_version}: {row.plan_sha256}\n"
                )
                if public_id:
                    typer.echo(row.plan_text)
        finally:
            engine.dispose()


@app.command("list")
def list_command(
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
) -> None:
    """List manually recorded publications without dumping plan snapshots."""
    display_records(config)


@app.command("show")
def show_command(
    public_id: str,
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Inspect a historical Publication and its exact plan snapshot."""
    display_records(config, public_id)
