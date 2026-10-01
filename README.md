# Swale Sounds

A local-first application intended to evolve into an automated, data-driven
long-form music and ambience production system.

Milestone 2 adds validated session specifications, canonical YAML, traceable
SQLite records, workspace creation, and CLI inspection to the foundation.
It runs on a developer workstation or directly on a conventional Linux VM.
Media production is not implemented.

## Prerequisites and setup

Install `uv`. Python 3.12+ is required; `.python-version` selects Python 3.12,
which uv downloads automatically if it is unavailable. FFmpeg will be needed
for later media milestones but is **not required for Milestone 2**.

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
`alembic_version`. Session creation allocates public identifiers automatically.
Lifecycle transitions are deferred. Status values are readable strings
validated by a SQLite check constraint. JSON stores specification snapshots.
ORM writes populate UTC timestamps and refresh `updated_at` on updates.
SQLite stores naive UTC, and the ORM restores timezone-aware UTC on reads.
Direct SQL writers must supply timestamps themselves.

Use `create_database_engine(config.database)` and
`create_session_factory(engine)` from `swale_sounds.database`. Each
`with factory.begin() as db:` block commits on success and rolls back on error,
closing its session afterward. Dispose the engine when finished.

## Session workflow

```bash
uv run swale-sounds session validate examples/rainy-paris.yaml
uv run swale-sounds session create examples/rainy-paris.yaml
uv run swale-sounds session list
uv run swale-sounds session show session-000001
```

Validation needs no application configuration or database and creates no state.
Create, list, and show accept `--config /path/to/settings.yaml` after the
subcommand. List returns sessions by ascending internal ID; show reports
metadata and the normalized content specification. Unknown IDs return an error.

Specifications require version 1, a nonblank string title, and an integer
duration of 1–720 minutes. Optional BPM bounds must be integers between 1 and
300 with maximum at least minimum. All five content sections and `spec_version`
are required; taxonomy lists default to empty, BPM and visual style to null.
Unknown fields are rejected. Taxonomy is open-ended: values are trimmed and
lowercased, whitespace/hyphen/underscore runs become one underscore, and
leading/trailing underscores are removed. Empty results are rejected. Unicode
is preserved; no semantic synonyms are inferred (`lo-fi` becomes `lo_fi`).

Canonical YAML uses sorted keys, explicit defaults, UTF-8 and LF newlines.
List order is preserved. Comments, quoting, indentation and input key order
do not affect the accepted representation. The persisted SHA-256 hashes the
exact canonical bytes written to `session.yaml`; `spec_json` contains the same
normalized structured data.

`session.yaml` describes desired content. SQLite records application state
and provenance. Runtime IDs, status, paths and timestamps stay out of the YAML.
Database workspace/specification paths are relative to the configured data root.

Creation allocates `session-000001`, `session-000002`, and so on under a SQLite
`BEGIN IMMEDIATE` write lock, using the largest existing numeric public ID.
The unique constraint remains authoritative. Concurrent writers serialize;
a busy database can return an actionable error for retry. No schema change
or additional dependency is required. Manual deletion of the highest ID can
allow reuse; deletion is not an application operation in this milestone.

Each workspace contains:

```text
data/sessions/session-000001/
├── session.yaml
├── music/source/
├── music/processed/
├── artwork/source/
├── ambience/source/
├── manifests/
├── intermediate/
├── logs/
└── output/
```

Existing workspaces are never merged or overwritten. Expected creation failures
roll back the row and remove only the newly created session workspace. Empty
parent directories may remain. If cleanup fails, the error names the directory
for inspection. SQLite and the filesystem are not one atomic transaction:
process termination or power loss may leave an orphan workspace, which must
be inspected before retrying. Creation fails safely if that path already exists.

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
