# Swale Sounds

A local-first application intended to evolve into an automated, data-driven
long-form music and ambience production system.

Milestone 3 adds source-media import, media inspection, provenance records,
and recoverable asset manifests to the validated session workflow.
It runs on a developer workstation or directly on a conventional Linux VM.
Media production is not implemented.

## Prerequisites and setup

Install `uv`. Python 3.12+ is required; `.python-version` selects Python 3.12,
which uv downloads automatically if it is unavailable. Install FFmpeg on the
host and ensure `ffprobe` is on `PATH` (for example, `brew install ffmpeg` on
macOS or your Linux distribution's FFmpeg package). This is an external
executable requirement, not a Python dependency. Verify with `ffprobe -version`.
It is used only for inspection; the application does not render media yet.

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

The migrations create `sessions` and `assets`; Alembic tracks revisions in
`alembic_version`. Session creation allocates public identifiers automatically.
Imports can advance `created` to `assets_ready`. Status values are readable strings
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

## Source asset import and provenance

Upgrade the database before using the asset commands:

```bash
uv run alembic upgrade head
uv run swale-sounds asset import session-000001 ./input/music --kind music
uv run swale-sounds asset import session-000001 ./input/cover.png --kind artwork
uv run swale-sounds asset import session-000001 ./input/rain.wav --kind ambience
uv run swale-sounds asset list session-000001
uv run swale-sounds asset manifest session-000001
```

All asset commands accept `--config /path/to/settings.yaml`. Imports accept a
single regular file or the direct files in a directory. Directory entries are
sorted by case-sensitive Unicode filename order; subdirectories are ignored.
Source symlinks, symlink ancestors, and symbolic links inside the workspace
are rejected. Empty directories and unsupported files fail the batch.

Music and ambience support WAV, FLAC, MP3, M4A, AAC and OGG. Artwork supports PNG,
JPG/JPEG and WebP. ffprobe must confirm an audio stream for audio or exactly one
frame in a supported still-image format for artwork; a renamed video does not
qualify. Animated artwork is not accepted. Inspection has a 60-second timeout.

Each invocation preflights all candidates before copying. Source hashes use
bounded-memory reads. Copies are created exclusively in `music/source`,
`artwork/source`, or `ambience/source`, preserving original filenames. Each
copy's SHA-256 is verified before its row can commit. Originals are not changed
and imported source files are never transformed or edited by the application.
Database paths are relative to the data root.

Asset identities use collision-resistant `asset-` identifiers independent of
filenames. A unique `(session_id, kind, sha256)` constraint makes repeated
imports idempotent within each session and kind, even under concurrent imports.
Duplicates are reported and retain their original provenance. Same filename
with different bytes fails; untracked existing destination files also fail
instead of being adopted or overwritten. An identical file can be imported
separately into another session or another kind.

New rows record original filename, hash, size, MIME hint, format and codec,
available duration/sample rate/channels or image dimensions, and UTC import
time. The following options record operator-supplied evidence for every new
asset in the batch:

```bash
uv run swale-sounds asset import session-000001 ./input/music --kind music \
  --provider example-provider --provider-model model-v1 --provider-plan plan \
  --licence-notes "Operator-supplied evidence" \
  --licence-url https://example.org/terms --licence-version v1 \
  --generation-prompt-file ./prompt.txt \
  --generation-parameters-file ./parameters.json
```

Provider defaults to `manual`. Other provenance fields are optional. Prompt
files are UTF-8 text; generation parameters must be a JSON object with finite
numbers. Provenance does not establish ownership, usage rights or Content ID
eligibility. Full original machine-specific source paths are not persisted.

SQLite foreign keys are enabled on every application connection. Assets have
a required Session foreign key, a session index, unique public IDs, unique
destination paths and unique session/kind/content combinations. Migration
`0002` adds this table without changing the existing Session schema.

A session advances from `created` to `assets_ready` only after at least one
music source and one artwork source have committed. Ambience is optional.
Import order does not matter, and other statuses are never downgraded.

## Asset manifests and failure recovery

The responsibilities are:

- SQLite: authoritative operational state and provenance.
- `session.yaml`: desired content specification.
- `manifests/assets.json`: derived representation of the session's Asset rows.
- Workspace source files: immutable imported media bytes.

The manifest has `manifest_version: 1`, the public session ID and asset records
ordered by kind, path and public ID. It includes all provenance fields,
including explicit nulls for unavailable values, and UTC import timestamps.
Sorted JSON keys, UTF-8, two-space indentation and LF newlines make unchanged
database state produce identical bytes. A temporary file in the manifest
directory is atomically replaced into place. Imports and manifest generation
serialize through SQLite's writer lock so competing snapshots cannot overwrite
a newer manifest with stale rows.

Before the database commit, any copy/hash/persistence failure rolls back the
batch and cleans up only its newly created files. Pre-existing data is retained.
Manifest generation happens after the authoritative commit: if it fails, the
CLI explicitly reports that assets were committed. Keep those assets and run
`uv run swale-sounds asset manifest session-000001` to regenerate from SQLite.
If using custom settings, pass the same `--config` to the recovery command.

Database and filesystem writes are not a crash-atomic transaction. Forced
termination may leave untracked source files; inspect them before retrying.
An import reports missing or changed tracked duplicate files instead of
silently accepting them. Manifests can always be regenerated from database
records, but regeneration does not repair or revalidate source media.

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
Real-media tests generate tiny WAV and PNG fixtures and invoke ffprobe. They
explicitly skip when ffprobe is unavailable; Milestone 3 development acceptance
requires running them with ffprobe installed. No binary fixtures are committed.
