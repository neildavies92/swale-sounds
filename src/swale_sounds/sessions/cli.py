"""Thin presentation layer for session operations."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
import yaml
from sqlalchemy.exc import IntegrityError, OperationalError

from swale_sounds.config import (
    DEFAULT_CONFIG_PATH,
    ConfigurationError,
    load_config,
)
from swale_sounds.database import create_database_engine
from swale_sounds.sessions.schema import SessionSpecError, load_spec
from swale_sounds.sessions.service import (
    SessionNotFoundError,
    create_session,
    get_session,
    list_sessions,
)
from swale_sounds.sessions.workspace import SessionWorkspaceError

app = typer.Typer(
    help="Validate, create and inspect local sessions.", no_args_is_help=True
)


@contextmanager
def command_errors() -> Iterator[None]:
    try:
        yield
    except (
        ConfigurationError,
        SessionSpecError,
        SessionNotFoundError,
        SessionWorkspaceError,
    ) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except (OperationalError, IntegrityError) as exc:
        typer.echo(
            f"Database operation failed: {exc.orig}. "
            "Check the configured database, permissions and locks; "
            "run 'uv run alembic upgrade head' if its schema is missing.",
            err=True,
        )
        raise typer.Exit(1) from exc


@app.command("validate")
def validate_spec(spec_path: Path) -> None:
    """Validate a specification without creating application state."""
    with command_errors():
        load_spec(spec_path)
        typer.echo(f"Valid session specification: {spec_path}")


@app.command("create")
def create(
    spec_path: Path,
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Create a persisted session and its canonical workspace."""
    with command_errors():
        settings = load_config(config)
        engine = create_database_engine(settings.database)
        try:
            row = create_session(engine, settings.paths.data, spec_path)
            typer.echo(f"Created {row.public_id}")
            typer.echo(
                f"Workspace: {settings.paths.data / row.workspace_path}"
            )
        finally:
            engine.dispose()


@app.command("list")
def list_command(
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
) -> None:
    """List sessions in creation order."""
    with command_errors():
        engine = create_database_engine(load_config(config).database)
        try:
            rows = list_sessions(engine)
            if not rows:
                typer.echo("No sessions found.")
                return
            typer.echo("ID\tTITLE\tSTATUS")
            for row in rows:
                typer.echo(f"{row.public_id}\t{row.title}\t{row.status.value}")
        finally:
            engine.dispose()


@app.command("show")
def show(
    public_id: str,
    config: Annotated[Path, typer.Option("--config")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Show persisted metadata and the normalized content specification."""
    with command_errors():
        engine = create_database_engine(load_config(config).database)
        try:
            row = get_session(engine, public_id)
            typer.echo(
                f"ID: {row.public_id}\nTitle: {row.title}\n"
                f"Status: {row.status.value}\n"
                f"Specification version: {row.spec_version}\n"
                f"Workspace: {row.workspace_path}\n"
                f"Specification: {row.spec_path}\n"
                f"SHA-256: {row.spec_sha256}\n"
                f"Created: {row.created_at.isoformat()}\n"
                "\nContent specification:"
            )
            typer.echo(
                yaml.safe_dump(
                    row.spec_json, sort_keys=True, allow_unicode=True
                )
            )
        finally:
            engine.dispose()
