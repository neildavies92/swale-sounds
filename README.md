# Swale Sounds

A local-first application for producing long-form music and ambience videos.
**Phase 1, the local production engine, is complete:** Session specifications,
source Asset provenance/import, continuous audio rendering, static-artwork video
rendering, and optional OpenAI artwork generation.

The project is moving into a publish/measure MVP: repeated, controlled YouTube
content experiments with minimal music-generation cost. YouTube Audio Library
is the default MVP music source, acquired manually by the operator. Original/AI
music is deferred until audience demand is validated. Local publication planning
and manual publication tracking are available; automatic uploading and analytics
are future work.

See the **[MVP strategy, success criteria, and roadmap](docs/mvp.md)**.
The existing production engine runs on a developer workstation or a conventional
Linux VM.

## Prerequisites and setup

Install `uv`. Python 3.12+ is required; `.python-version` selects Python 3.12,
which uv downloads automatically if it is unavailable. Install FFmpeg on the
host and ensure `ffmpeg` and `ffprobe` are on `PATH` (for example, `brew install ffmpeg` on
macOS or your Linux distribution's FFmpeg package). This is an external
executable requirement, not a Python dependency. Verify with `ffmpeg -version`
and `ffprobe -version`.

Run commands from the repository root:

```bash
make setup
make check
make smoke
```

`make setup` runs `uv sync`. `make check` verifies formatting, lint, strict mypy
and pytest. `make smoke` runs the real CLI through a fresh isolated database,
one-minute Session, generated music and portrait artwork, and a verified
640×360 H.264/AAC MP4 and a validated publication plan. FFmpeg and ffprobe must
be on PATH; missing tools fail
the smoke test rather than skipping it.

GitHub Actions runs these same three commands on pull requests and pushes to
`main`, using Ubuntu, Python from `.python-version`, uv, and FFmpeg/ffprobe.
Application tests use mocked artwork providers and require no provider or
YouTube credentials.

Smoke state lives in a temporary directory, never the normal `data/` workspace.
Success removes it; failure retains it and reports the failed step, command
output and location, including any RenderRun logs for inspection.

Run `make` or `make help` for the command list. Individual targets are
`make format` (apply formatting), `make lint` (format/lint checks),
`make typecheck` (strict mypy), and `make test` (pytest). `make migrate` upgrades
the normal configured local database. `make clean` removes only the root
pytest, mypy and Ruff caches; it preserves application data and `.venv`.
Use `make check smoke` before merging substantial work. These targets wrap the
existing commands; direct `uv run ...` usage remains supported.

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

The migrations create `sessions`, `assets`, and `render_runs`; Alembic tracks revisions in
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

## Publication planning

Produce a local YouTube publication package from a current successful video:

```bash
uv run swale-sounds session create examples/rainy-paris.yaml
# Use the Session ID printed above; verify licence terms before importing.
uv run swale-sounds asset import session-000001 ./input/music --kind music \
  --provider youtube_audio_library \
  --licence-notes "Attribution: not required; Track: TITLE by ARTIST; checked: DATE"
uv run swale-sounds asset import session-000001 ./input/cover.png --kind artwork
uv run swale-sounds render audio session-000001
uv run swale-sounds render video session-000001
uv run swale-sounds publish plan session-000001
```

Run migrations first as described above. The plan command also accepts
`--config /path/to/settings.yaml`. Review the resulting
`data/sessions/session-000001/output/publish/youtube.json`, then manually upload
the referenced MP4 through YouTube Studio and review its title, description,
and tags. **Planning never uploads anything.** A plan is publishing intent,
not evidence of publication. After manually uploading the reviewed package,
record the actual YouTube video ID:

```bash
uv run swale-sounds publish record session-000001 --video-id YOURVIDEOID \
  --published-at 2026-10-06T12:00:00+01:00
uv run swale-sounds publish list
uv run swale-sounds publish show publication-ID
```

Replace `YOURVIDEOID` with the actual 11-character ID. All commands accept
`--config`. Publication time defaults to now; explicit times must include a
timezone and are stored as UTC. The canonical watch URL is derived from the ID.
This is an operator attestation, not a YouTube verification or upload. Analytics
remain future work. The smoke test uses a synthetic ID without contacting YouTube.

Run `make migrate` for migration `0005`. Each Publication links its Session and
successful video RenderRun and stores the exact canonical plan text, version,
and SHA-256. Recording verifies the existing package and media against current
intent; it never regenerates a plan. Missing, edited, stale, or damaged packages
fail: repair and review against the actual upload before recording. Do not
regenerate a different package and treat it as evidence of an earlier upload.
An identical external ID/provenance returns the existing row and retains its
original timestamp; conflicting provenance fails. Different external IDs may
refer to the same content. History is append-only, with database protections
against updates/deletes and foreign-key deletion. Downgrade refuses while any
Publication exists. `show` includes the historical snapshot even after the
current package changes; `list` gives identities, URL, time, and plan hash.

Version 1 records the Session ID/specification hash, audio and video RenderRun
IDs, video path/hash, source Asset paths/hashes/IDs, music provenance, metadata,
and the complete normalized Session specification for future correlation.
All media paths are relative to the **Session workspace**, identified by
`path_base: "session_workspace"`, not to `output/publish/`. The MP4 stays in its
authoritative render location; no large copy or media symlink is created.
The artwork reference is source artwork, **not a validated YouTube thumbnail**.
Playlist intents are empty. No database rows or statuses are changed.

Planning shares video fingerprint resolution with rendering and checks canonical
specification integrity, current audio/configuration, source hashes, video hash,
size, and probed media metadata. Missing, stale, or damaged output is rejected
with repair guidance. FFmpeg/ffprobe remain required; a changed FFmpeg version
can require a new video render, matching rendering's existing reuse rules.
The existing lifecycle disallows audio rendering after `video_rendered`.
If replacement music or audio settings require new audio at that stage, create
a new Session and import its sources. Planning preserves that lifecycle and
rejects the stale video rather than changing status or rendering implicitly.

JSON uses UTF-8, sorted keys, two-space indentation, and a final newline. It has
no generation timestamp. Identical inputs produce identical bytes and leave an
existing identical file untouched. A changed recognized plan for the same
Session is replaced atomically. Unrecognized files, noncanonical edits, and
symlinks are rejected; move an edited plan aside before regenerating it. Other
package files are left alone. Keep operator edits separately from this derived
artifact and rerun planning before uploading to recheck integrity.

Metadata generation is local and deterministic. Titles use the Session title,
one mood/genre, and up to two purposes, with readable taxonomy separators and
a 100-character cap. Tags follow dimension/list order, deduplicate, and fit the
500-character budget including separators and implied quotes. Descriptions
retain recorded licence text and fail beyond 5000 UTF-8 bytes or for unsupported
angle brackets rather than silently cutting credits. These limits follow the
[YouTube video resource documentation](https://developers.google.com/youtube/v3/docs/videos#snippet).

Music provenance is provider-agnostic. The package includes public Asset IDs,
original filenames, provider/model, and licence notes/URL/version. Generation
prompts, parameters, provider plans, and provider responses are excluded.
Licence fields are intended for public review: record only publishable evidence,
never credentials or private paths. The planner does not infer rights from a
provider name or claim ownership.

When licence notes are present, explicitly record one of these declarations
(case-insensitive, delimited by a newline or semicolon):

```text
Attribution: not required
```

or, when required:

```text
Attribution: required
Attribution text: Exact credit supplied by the licence holder
```

Required credits must be recorded on the separate `Attribution text:` line.
The complete notes are retained in the description. Conflicting declarations,
unstructured notes without a declaration, and missing required credits fail
with the Asset ID and an actionable error. Empty notes are represented as
unknown requirements with an explicit review warning, never as permission.
Import accurate evidence up front with `--licence-notes`; the existing duplicate
import rule does not update provenance. For an already imported Asset with
incomplete evidence, correct the authoritative SQLite provenance through your
reviewed maintenance process or create a new Session and reimport with complete
notes; no provenance editing command is added here.

## Source asset import and provenance

For the MVP, manually acquire music from YouTube Audio Library and prefer tracks
that do not require attribution. Import downloaded files with the existing
command, recording the licence evidence you checked (replace the example notes
with the actual track details and terms):

```bash
uv run swale-sounds asset import session-000001 ./input/music --kind music \
  --provider youtube_audio_library \
  --licence-notes "Attribution: not required; Track: TITLE by ARTIST; checked: DATE"
```

Notes apply to every new Asset in the batch; import tracks separately when their
evidence differs. This provider label records provenance, not a downloading
integration or a grant of rights. See the [MVP sourcing strategy](docs/mvp.md).

Use the example's attribution declaration only when the checked terms support it.

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

## Artwork generation

For a Session in `created` with no artwork source, inspect generation intent:

```bash
uv run swale-sounds generate artwork session-000001 --dry-run
export OPENAI_API_KEY=...
uv run swale-sounds generate artwork session-000001
uv run swale-sounds asset list session-000001
```

API usage incurs provider charges. Dry-run requires no credentials, makes no
API call, and creates no files, Assets or Session changes. The key is read only
from `OPENAI_API_KEY`; never put it in YAML, CLI arguments or provenance.

Session metadata becomes a deterministic still-image prompt: title, mood,
purpose, environment, location, weather, time, season, visual style and optional
animation values as still-image motifs. Empty sections are omitted. The default
composition discourages text, logos, borders and readable signage. No additional
text-generation call is made. The exact submitted prompt is stored as provenance,
so prompts should not contain secrets.

Use `--prompt-file ./prompt.txt` for an exact UTF-8 override (including whitespace
and line endings), up to 32,000 characters and containing non-whitespace text.
Use `--config /path/to/settings.yaml` to override the optional `generation.artwork`
section. Phase 1 configurations that omit this section retain valid defaults.

The default is the dated `gpt-image-2.5-flare-2026-09-08` model at `1536x864`,
medium quality, PNG, opaque background and automatic moderation. These model and
size settings were verified against the [official Image API reference](https://developers.openai.com/api/reference/python/resources/images/methods/generate).
Model IDs remain configurable; provider-side model/size limits remain authoritative.
The official Python SDK (`openai>=3.22.1,<4`) calls `client.images.generate` once
with `n=1`, streaming disabled, a configured 180-second timeout and SDK retries
disabled (`max_retries=0`). There is no application retry loop. After an ambiguous
timeout, check provider usage before explicitly invoking generation again.

The generated image is staged under `intermediate/generation-<uuid>/` and passed
to the existing Asset importer. Normal ffprobe still-image validation, SHA-256,
safe copying, manifest regeneration and readiness rules all apply. With existing
music, the import advances the Session to `assets_ready`; without music it stays
`created`. Existing artwork is never replaced or automatically supplemented;
the video renderer still requires exactly one artwork source. The importer
rechecks the created/no-artwork condition under its writer lock to protect
against concurrent state changes during generation. Concurrent requests can
still incur provider charges; a result that loses the import race is retained.

The Asset records `provider=openai`, the exact configured model, submitted prompt,
request parameters (size, quality, format, background, moderation and image count),
and available response metadata (actual size/quality/format/background and revised
prompt). The submitted prompt is never replaced by a provider revision. Raw API
responses and base64 are not stored. Provider plan remains unset. Licence fields
remain unset unless evidence is supplied using
`--licence-notes`, `--licence-url` and `--licence-version`. Generation does not
establish legal ownership or Content ID rights.

Success removes staging; provider/decoding failures remove empty staging and
create no Asset. If import or manifest creation fails after generation, the CLI
reports the retained candidate path for manual recovery. An error stating
`Assets committed` means the Asset exists already: regenerate its manifest using
`swale-sounds asset manifest`, rather than making another paid request. Interrupted
processes may leave staging for inspection. No generation table or migration is
needed; successful provenance belongs to the Asset.

`make check`, pytest and `make smoke` never make paid provider calls. Provider
tests use fake SDK responses, while the existing smoke remains the local Phase 1
workflow with generated test fixtures.

## Continuous audio rendering

After importing music and artwork to reach `assets_ready`, run:

```bash
uv run swale-sounds render audio session-000001
uv run swale-sounds render audio session-000001 --force
uv run swale-sounds render audio session-000001 --config /path/to/settings.yaml
```

Rendering accepts `assets_ready` and `audio_rendered` sessions. It requires at
least one music Asset and zero or one ambience Asset. It does not read artwork.
Preflight verifies the canonical specification and the selected source paths
and hashes. Symlinks, missing files and changed sources are rejected.

Music is ordered by Asset path, then public ID. FFmpeg selects the first audio
stream, normalizes each track to the configured sample rate and channels in
FLAC, and concatenates them with hard transitions. The sequence repeats or
trims to the specification's duration in minutes. Optional ambience is normalized,
looped, and mixed at the configured decibel gain. There is no crossfade,
mastering or loudness normalization; the additive mix can clip.

The final output is AAC in M4A, using the configured bitrate, sample rate and
channels. Other configured audio codecs are rejected. Metadata is stripped.
Before publication, ffprobe verifies the codec, rate, channels and duration
within 0.25 seconds of the target. Publication is atomic and cannot overwrite
an existing output. Each successful attempt has a unique path:
`output/audio/render-<uuid>.m4a`. Temporary files in
`intermediate/render-<uuid>/` are removed after success or expected failure;
command arguments and FFmpeg diagnostics remain in the run's log under `logs/`.
Encoding has no short timeout and streams diagnostics directly to disk.

Migration `0003` adds RenderRun without changing earlier migrations or Assets.
Each run belongs to a Session and records its stage, status, renderer version,
input/configuration snapshots, fingerprint, FFmpeg version, log path, UTC start
and finish times, error, and successful output path, hash, size and audio metadata.
Assets and `manifests/assets.json` remain source-only; rendered media is recorded
only in RenderRun.

The deterministic fingerprint includes renderer version 1, specification SHA-256,
ordered source identities/paths/hashes, ambience, audio settings and the FFmpeg
version line. A matching successful run is reused only after its output path
and SHA-256 pass validation. Missing or changed cached output produces an error
suggesting `--force`. Force creates a new run and output, preserving earlier
outputs and history. Changed inputs or settings also create a new fingerprint.
Identical bytes across different FFmpeg builds are not guaranteed.

A running row commits before media processing starts. A partial unique index
allows one running audio render per Session; the database write lock is released
before FFmpeg runs. Success advances `assets_ready` to `audio_rendered`.
Expected processing failures retain a failed row and log, remove attempt-owned
temporary/output files, and leave Session status unchanged. Preflight failures
do not create a run. Sources are checked again before publication.

SQLite and filesystem publication cannot form one crash-atomic transaction.
Forced termination can leave a running row, intermediates or an orphan output.
Inspect the run and log and confirm no renderer is active before manually
repairing interrupted state. Automatic restart, cancellation and stale-run
recovery are outside this milestone.

## Long-form video rendering

Upgrade the database, import music and exactly one artwork source, then run
the two rendering stages explicitly:

```bash
uv run alembic upgrade head
uv run swale-sounds render audio session-000001
uv run swale-sounds render video session-000001
uv run swale-sounds render video session-000001 --force
```

Video accepts `audio_rendered` and `video_rendered` sessions. All commands accept
`--config /path/to/settings.yaml`. The renderer combines exactly one static
artwork Asset with a current successful audio RenderRun. Multiple artworks
are not yet supported; `visual.animation` remains descriptive metadata and
is not executed. Video generation never rebuilds or remixes audio.

The renderer reproduces the audio fingerprint from the current specification,
ordered music/ambience Asset records, audio configuration, current audio renderer
version, and each candidate's recorded FFmpeg version. It selects the newest
matching successful audio run. New sources or changed audio settings make old
audio stale even if the Session status still says `audio_rendered`; run the
audio stage first. An FFmpeg upgrade alone does not invalidate existing audio
for video use. The selected audio file must pass safe-path, SHA-256 and audio
metadata verification. Video reads the derived audio file, not source music.

FFmpeg loops the artwork at `media.video.fps`, scales it to fit inside
`media.video.width` × `media.video.height`, and pads centrally with black while
preserving aspect ratio. Dimensions must be positive even integers for yuv420p.
The output uses libx264/H.264, medium preset, stillimage tune, yuv420p, and copies
the existing AAC stream into MP4 with faststart. Metadata and chapters are
stripped. Rendering ends at the verified audio duration.

Before publication, ffprobe must confirm exactly one H.264 video stream and one
AAC audio stream, the requested dimensions, pixel format, frame rate, sample
rate and channels. Container and stream durations must be within 0.25 seconds
of the verified audio duration. Artwork and audio hashes are checked again.
Each output is published exclusively to `output/video/render-<uuid>.mp4`.
Source artwork remains unchanged.

The provenance chain is:

```text
Session specification → source Assets → audio RenderRun → video RenderRun
                              artwork Asset ────────────────────┘
```

Assets are immutable imported sources. Audio RenderRuns describe derived
continuous audio; video RenderRuns describe final video. `assets.json` stays
source-only. Video input snapshots reference the audio run's public ID, path,
hash and fingerprint plus the artwork Asset's identity, path and hash. Its
configuration snapshot records dimensions/FPS and encoder, format and copy
semantics. Video renderer version 1 and the current FFmpeg version participate
in its fingerprint; final output SHA-256 completes the provenance chain.

An identical valid request reuses the matching successful video. Changed
video settings create a new run. `--force` creates another immutable output,
preserving historical runs and files. Missing, unsafe or changed cached output
fails with guidance to use `--force`; history is never silently repaired.

Migration `0004` recreates the SQLite render table through Alembic batch
alteration, extending the stage constraint to `audio`/`video` and adding nullable
positive `width`, `height` and `frame_rate` columns. Existing audio rows and
fingerprints are preserved. Downgrade refuses to discard existing video history.
One running render per Session and stage is permitted, so audio and video do
not share a global lock. A running row commits before encoding starts.

Success advances `audio_rendered` to `video_rendered`; repeat renders retain
`video_rendered`. Failures preserve Session status and retain the failed run
and log while cleaning attempt-owned media. Preflight failures create no run.
The interrupted-process and non-atomic filesystem/database limitations described
above also apply to video. No automatic publishing or background worker is used.

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
Real-media tests generate WAV and PNG fixtures and invoke FFmpeg/ffprobe,
including one-minute audio renders. Media tests explicitly skip when required
executables are unavailable; development acceptance requires both executables
installed and no media-test skips. No binary fixtures are committed.
