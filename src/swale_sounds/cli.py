"""Command-line entry point."""

from importlib.metadata import version as package_version

import typer

from swale_sounds.assets.cli import app as asset_app
from swale_sounds.generation.cli import app as generation_app
from swale_sounds.publishing.cli import app as publish_app
from swale_sounds.rendering.cli import app as render_app
from swale_sounds.sessions.cli import app as session_app

app = typer.Typer(no_args_is_help=True)
app.add_typer(session_app, name="session")
app.add_typer(asset_app, name="asset")
app.add_typer(render_app, name="render")
app.add_typer(generation_app, name="generate")
app.add_typer(publish_app, name="publish")


@app.callback()
def main() -> None:
    """Swale Sounds: local music and ambience production foundation."""


@app.command()
def version() -> None:
    """Show the installed application version."""
    typer.echo(package_version("swale-sounds"))
