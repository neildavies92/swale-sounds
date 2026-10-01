"""Asset commands following the established session CLI conventions."""

from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import Engine

from swale_sounds.assets.manifest import regenerate_manifest
from swale_sounds.assets.provenance import AssetError, load_provenance
from swale_sounds.assets.service import import_assets, list_assets
from swale_sounds.config import DEFAULT_CONFIG_PATH, AppConfig, load_config
from swale_sounds.database import create_database_engine
from swale_sounds.models import AssetKind
from swale_sounds.sessions.cli import command_errors

app = typer.Typer(
    help="Import source media and inspect its provenance.",
    no_args_is_help=True,
)


class ImportKind(StrEnum):
    MUSIC = "music"
    ARTWORK = "artwork"
    AMBIENCE = "ambience"


@contextmanager
def asset_command(config: Path) -> Iterator[tuple[AppConfig, Engine]]:
    with command_errors():
        try:
            settings = load_config(config)
            engine = create_database_engine(settings.database)
            try:
                yield settings, engine
            finally:
                engine.dispose()
        except AssetError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc


@app.command("import")
def import_command(
    public_id: str,
    source: Path,
    kind: Annotated[ImportKind, typer.Option("--kind")],
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
    provider: str = "manual",
    provider_model: str | None = None,
    provider_plan: str | None = None,
    licence_notes: str | None = None,
    licence_url: str | None = None,
    licence_version: str | None = None,
    generation_prompt_file: Path | None = None,
    generation_parameters_file: Path | None = None,
) -> None:
    """Import a file or one directory level as a single validated batch."""
    with asset_command(config) as (settings, engine):
        provenance = load_provenance(
            provider=provider,
            provider_model=provider_model,
            provider_plan=provider_plan,
            licence_notes=licence_notes,
            licence_url=licence_url,
            licence_version=licence_version,
            prompt_file=generation_prompt_file,
            parameters_file=generation_parameters_file,
        )
        result = import_assets(
            engine,
            settings.paths.data,
            public_id,
            source,
            AssetKind(f"{kind.value}_source"),
            provenance,
        )
        typer.echo(
            f"Imported {result.imported} {kind.value} assets into {public_id}"
        )
        for filename in result.skipped:
            typer.echo(f"Skipped duplicate: {filename}")
        typer.echo(f"Manifest: {result.manifest}")


@app.command("list")
def list_command(
    public_id: str,
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
) -> None:
    """List a session's assets in deterministic kind/path/ID order."""
    with asset_command(config) as (_, engine):
        assets = list_assets(engine, public_id)
        if not assets:
            typer.echo(f"No assets found for {public_id}.")
            return
        typer.echo("ID\tKIND\tFILE\tSIZE (bytes)\tDURATION (s)")
        for asset in assets:
            duration = (
                f"{asset.duration_seconds:.2f}"
                if asset.duration_seconds is not None
                else "-"
            )
            typer.echo(
                f"{asset.public_id}\t{asset.kind.value}\t{asset.original_filename}\t"
                f"{asset.size_bytes}\t{duration}"
            )


@app.command("manifest")
def manifest_command(
    public_id: str,
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Regenerate the asset manifest from authoritative SQLite state."""
    with asset_command(config) as (settings, engine):
        path = regenerate_manifest(engine, settings.paths.data, public_id)
        typer.echo(f"Manifest: {path}")
