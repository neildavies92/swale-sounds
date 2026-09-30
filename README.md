# Swale Sounds

A local-first application intended to evolve into an automated, data-driven
long-form music and ambience production system.

Milestone 1 provides the Python project, validated configuration, SQLite
persistence, migrations, CLI bootstrap, and tests. Session creation and media
production are not implemented. It runs on a developer workstation or directly
on a conventional Linux VM; no hosted service is required.

## Prerequisites and setup

Install `uv`. Python 3.12+ is required; `.python-version` selects Python 3.12,
which uv downloads automatically if it is unavailable. FFmpeg will be needed
for later media milestones but is **not required for Milestone 1**.

Run commands from the repository root:

```bash
uv sync
uv run alembic upgrade head
uv run swale-sounds --help
uv run swale-sounds version
```

`uv sync` manages dependencies in the project-local `.venv/`. No global project
packages or manual environment activation are required. Commit `uv.lock` so
other workstations and Linux VMs resolve the same dependency versions.

## Configuration and persistence

Settings are loaded with `load_config(path)` from `swale_sounds.config`.
The default is `config/swale-sounds.yaml`. All fields are required, unknown
fields are rejected, and YAML is parsed safely. Relative filesystem paths,
including SQLite database paths, resolve against the **working directory**,
even when an explicit configuration file is supplied. `~` is expanded.
Configuration loading does not create files or directories.

The default database is `data/swale-sounds.db`. Alembic creates its parent
directory and manages the schema; application engine creation never creates
tables. To migrate using another configuration:

```bash
uv run alembic -x config=/path/to/settings.yaml upgrade head
```

The initial migration creates `sessions`; Alembic also tracks revisions in
`alembic_version`. Public identifiers must be supplied by callers; allocation
and lifecycle transitions are deferred. Status values are readable strings
validated by a SQLite check constraint. JSON stores specification snapshots.
ORM writes populate UTC timestamps and refresh `updated_at` on updates.
SQLite stores naive UTC, and the ORM restores timezone-aware UTC on reads.
Direct SQL writers must supply timestamps themselves.

Use `create_database_engine(config.database)` and
`create_session_factory(engine)` from `swale_sounds.database`. Each
`with factory.begin() as db:` block commits on success and rolls back on error,
closing its session afterward. Dispose the engine when finished.

## Development checks

```bash
uv run pytest
uv run ruff format .
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

Tests use temporary configuration files and SQLite databases, including real
Alembic upgrades, schema comparison, and downgrade/upgrade coverage.
