"""Operator artwork generation and credential-free intent preview."""

import json
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.engine import make_url

from swale_sounds.config import DEFAULT_CONFIG_PATH, load_config
from swale_sounds.database import create_database_engine
from swale_sounds.generation.artwork import generate_artwork, prepare_artwork
from swale_sounds.generation.openai_images import (
    GenerationError,
    request_parameters,
)
from swale_sounds.sessions.cli import command_errors

app = typer.Typer(
    help="Generate source media with recorded provenance.",
    no_args_is_help=True,
)


@app.command("artwork")
def artwork_command(
    public_id: str,
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    prompt_file: Annotated[Path | None, typer.Option("--prompt-file")] = None,
    licence_notes: str | None = None,
    licence_url: str | None = None,
    licence_version: str | None = None,
) -> None:
    """Generate one artwork source, or preview the exact request for free."""
    with command_errors():
        try:
            settings = load_config(config)
            database = make_url(settings.database.url).database
            if database != ":memory:" and (
                database is None or not Path(database).is_file()
            ):
                raise GenerationError(
                    "Session database is missing; run migrations "
                    "and create a Session first."
                )
            engine = create_database_engine(settings.database)
            try:
                if dry_run:
                    plan = prepare_artwork(
                        engine, settings, public_id, prompt_file
                    )
                    request = json.dumps(
                        request_parameters(plan.settings), sort_keys=True
                    )
                    typer.echo(
                        f"Artwork generation preview\n\nSession: {public_id}\n"
                        f"Provider: {plan.settings.provider}\n"
                        f"Model: {plan.settings.model}\nRequest: {request}\n"
                        f"Timeout: {plan.settings.timeout_seconds:g}s; "
                        f"automatic retries: 0\n\nPrompt:\n{plan.prompt}"
                    )
                    return
                result = generate_artwork(
                    engine,
                    settings,
                    public_id,
                    prompt_file=prompt_file,
                    licence_notes=licence_notes,
                    licence_url=licence_url,
                    licence_version=licence_version,
                )
                typer.echo(
                    f"Generated artwork for {public_id}\n\n"
                    f"Provider: {result.asset.provider}\n"
                    f"Model: {result.asset.provider_model}\n"
                    f"Asset: {result.asset.public_id}\n"
                    f"Output: {settings.paths.data / result.asset.path}\n"
                    f"Manifest: {result.manifest}"
                )
            finally:
                engine.dispose()
        except GenerationError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
