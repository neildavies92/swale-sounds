"""Command-line entry point."""

from importlib.metadata import version as package_version

import typer

from swale_sounds.assets.cli import app as asset_app
from swale_sounds.sessions.cli import app as session_app

app = typer.Typer(no_args_is_help=True)
app.add_typer(session_app, name="session")
app.add_typer(asset_app, name="asset")


@app.callback()
def main() -> None:
    """Swale Sounds: local music and ambience production foundation."""


@app.command()
def version() -> None:
    """Show the installed application version."""
    typer.echo(package_version("swale-sounds"))
