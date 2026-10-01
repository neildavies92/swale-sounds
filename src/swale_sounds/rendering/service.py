"""Reserve attempts, verify provenance and publish immutable audio outputs."""

import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue, ValidationError
from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import SQLAlchemyError

from swale_sounds.assets.files import (
    calculate_sha256,
    safe_child,
    safe_workspace,
)
from swale_sounds.assets.probe import MediaProbeError, MediaStream
from swale_sounds.assets.provenance import AssetError
from swale_sounds.config import AppConfig
from swale_sounds.database import create_session_factory
from swale_sounds.models import (
    Asset,
    AssetKind,
    RenderRun,
    RenderStage,
    RenderStatus,
    Session,
    SessionStatus,
)
from swale_sounds.models.session import utc_now
from swale_sounds.rendering.audio import (
    AUDIO_RENDERER_VERSION,
    AudioSettings,
    render_pipeline,
    verify_output,
)
from swale_sounds.rendering.ffmpeg import (
    RenderError,
    find_ffmpeg,
    open_render_log,
)
from swale_sounds.sessions.schema import (
    SessionSpec,
    canonical_bytes,
    sha256_bytes,
)
from swale_sounds.sessions.service import SessionNotFoundError


def input_fingerprint(
    inputs: dict[str, JsonValue],
    configuration: dict[str, JsonValue],
    ffmpeg_version: str,
    renderer_version: int = AUDIO_RENDERER_VERSION,
) -> str:
    payload = {
        "inputs": inputs,
        "configuration": configuration,
        "renderer_version": renderer_version,
        "ffmpeg_version": ffmpeg_version,
    }
    return sha256_bytes(
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def persisted_path(data_root: Path, workspace: Path, relative: str) -> Path:
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts:
        raise RenderError(f"Unsafe persisted render/input path: {relative}")
    try:
        child = (data_root.resolve() / part).relative_to(workspace)
    except ValueError as exc:
        raise RenderError(
            f"Path is outside the session workspace: {relative}"
        ) from exc
    return safe_child(workspace, child.as_posix())


def verify_source(data_root: Path, workspace: Path, asset: Asset) -> Path:
    path = persisted_path(data_root, workspace, asset.path)
    if not path.is_file():
        raise RenderError(
            f"Source asset is missing or not a regular file: {asset.path}"
        )
    if calculate_sha256(path) != asset.sha256:
        raise RenderError(
            f"Source asset has changed since import: {asset.original_filename}"
        )
    return path


def asset_snapshot(asset: Asset) -> dict[str, JsonValue]:
    return {
        "asset_id": asset.public_id,
        "path": asset.path,
        "sha256": asset.sha256,
    }


@dataclass(frozen=True)
class RenderResult:
    run: RenderRun
    reused: bool


@dataclass(frozen=True)
class RenderPlan:
    run: RenderRun
    workspace: Path
    executable: str
    music: list[Path]
    ambience: Path | None
    settings: AudioSettings
    source_hashes: dict[Path, str]


def commit_or_discard(connection: Connection) -> None:
    committed = False
    try:
        connection.commit()
        committed = True
    finally:
        if not committed:
            connection.invalidate()


def reserve_render(
    engine: Engine,
    settings: AppConfig,
    public_id: str,
    force: bool,
) -> RenderPlan | RenderResult:
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        with create_session_factory(engine)(bind=connection) as db:
            session = db.scalar(
                select(Session).where(Session.public_id == public_id)
            )
            if session is None:
                raise SessionNotFoundError(f"Session not found: {public_id}")
            if session.status not in {
                SessionStatus.ASSETS_READY,
                SessionStatus.AUDIO_RENDERED,
            }:
                raise RenderError(
                    f"Session {public_id} is not ready for audio rendering; "
                    f"status is {session.status.value}."
                )
            active = db.scalar(
                select(RenderRun).where(
                    RenderRun.session_id == session.id,
                    RenderRun.stage == RenderStage.AUDIO,
                    RenderRun.status == RenderStatus.RUNNING,
                )
            )
            if active is not None:
                raise RenderError(
                    f"Audio render already running: {active.public_id}. "
                    "Inspect its log before retrying."
                )
            workspace = safe_workspace(
                settings.paths.data, session.workspace_path
            )
            spec = SessionSpec.model_validate(session.spec_json)
            spec_path = persisted_path(
                settings.paths.data, workspace, session.spec_path
            )
            if (
                not spec_path.is_file()
                or calculate_sha256(spec_path) != session.spec_sha256
                or sha256_bytes(canonical_bytes(spec)) != session.spec_sha256
            ):
                raise RenderError(
                    "Session specification provenance no longer matches "
                    "disk/database state."
                )
            assets = list(
                db.scalars(
                    select(Asset)
                    .where(
                        Asset.session_id == session.id,
                        Asset.kind.in_([AssetKind.MUSIC, AssetKind.AMBIENCE]),
                    )
                    .order_by(Asset.path, Asset.public_id)
                )
            )
            music = [item for item in assets if item.kind == AssetKind.MUSIC]
            ambience = [
                item for item in assets if item.kind == AssetKind.AMBIENCE
            ]
            if not music:
                raise RenderError(
                    "Audio rendering requires at least one music source."
                )
            if len(ambience) > 1:
                raise RenderError(
                    "Audio rendering supports at most one ambience source; "
                    f"found {len(ambience)}."
                )
            paths = {
                item.id: verify_source(settings.paths.data, workspace, item)
                for item in assets
            }
            media = settings.media
            if media.audio.codec != "aac":
                raise RenderError(
                    f"Unsupported audio codec: {media.audio.codec}. "
                    "Milestone 4 requires aac."
                )
            audio_settings = AudioSettings(
                target_duration_seconds=spec.output.duration_minutes * 60,
                sample_rate=media.sample_rate,
                channels=media.channels,
                codec=media.audio.codec,
                bitrate=media.audio.bitrate,
                ambience_gain_db=media.ambience.gain_db,
            )
            executable, version = find_ffmpeg()
            inputs: dict[str, JsonValue] = {
                "spec_sha256": session.spec_sha256,
                "music": [asset_snapshot(item) for item in music],
                "ambience": asset_snapshot(ambience[0]) if ambience else None,
            }
            configuration = audio_settings.model_dump(mode="json")
            fingerprint = input_fingerprint(
                inputs, configuration, version, AUDIO_RENDERER_VERSION
            )
            if not force:
                existing = db.scalar(
                    select(RenderRun)
                    .where(
                        RenderRun.session_id == session.id,
                        RenderRun.stage == RenderStage.AUDIO,
                        RenderRun.status == RenderStatus.SUCCEEDED,
                        RenderRun.input_fingerprint == fingerprint,
                    )
                    .order_by(RenderRun.id.desc())
                )
                if existing is not None:
                    try:
                        output = persisted_path(
                            settings.paths.data,
                            workspace,
                            existing.output_path or "",
                        )
                        if (
                            not output.is_file()
                            or calculate_sha256(output)
                            != existing.output_sha256
                        ):
                            raise RenderError(
                                "Output is missing or its SHA-256 has changed."
                            )
                    except (OSError, AssetError, RenderError) as exc:
                        raise RenderError(
                            "Recorded output provenance no longer matches "
                            f"disk state for {existing.public_id}: {exc}. "
                            "Use --force for a new output."
                        ) from exc
                    db.expunge(existing)
                    return RenderResult(existing, reused=True)
            render_id = f"render-{uuid4().hex}"
            run = RenderRun(
                public_id=render_id,
                session_id=session.id,
                stage=RenderStage.AUDIO,
                status=RenderStatus.RUNNING,
                renderer_version=AUDIO_RENDERER_VERSION,
                input_fingerprint=fingerprint,
                ffmpeg_version=version,
                inputs_json=inputs,
                configuration_json=configuration,
                log_path=f"{session.workspace_path}/logs/{render_id}.log",
            )
            db.add(run)
            db.flush()
            db.expunge(run)
            commit_or_discard(connection)
            return RenderPlan(
                run,
                workspace,
                executable,
                [paths[item.id] for item in music],
                paths[ambience[0].id] if ambience else None,
                audio_settings,
                {paths[item.id]: item.sha256 for item in assets},
            )


@contextmanager
def intermediate_directory(path: Path) -> Iterator[None]:
    path.mkdir(exist_ok=False)
    try:
        yield
    finally:
        shutil.rmtree(path)


def record_failure(engine: Engine, run_id: int, message: str) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        with create_session_factory(engine)(bind=connection) as db:
            run = db.get(RenderRun, run_id)
            if run is not None and run.status == RenderStatus.RUNNING:
                run.status = RenderStatus.FAILED
                run.finished_at = utc_now()
                run.error_message = message
                db.flush()
                commit_or_discard(connection)


def record_success(
    engine: Engine,
    run_id: int,
    output_path: str,
    digest: str,
    size: int,
    stream: MediaStream,
    duration: float,
) -> RenderRun:
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        with create_session_factory(engine)(bind=connection) as db:
            run = db.get(RenderRun, run_id)
            if run is None or run.status != RenderStatus.RUNNING:
                raise RenderError(
                    "Render state changed; refusing to rewrite history."
                )
            run.status = RenderStatus.SUCCEEDED
            run.finished_at = utc_now()
            run.output_path = output_path
            run.output_sha256 = digest
            run.output_size_bytes = size
            run.duration_seconds = duration
            run.sample_rate = stream.sample_rate
            run.channels = stream.channels
            run.codec_name = stream.codec_name
            session = db.get(Session, run.session_id)
            if session is not None and session.status in {
                SessionStatus.ASSETS_READY,
                SessionStatus.AUDIO_RENDERED,
            }:
                session.status = SessionStatus.AUDIO_RENDERED
            db.flush()
            db.expunge(run)
            commit_or_discard(connection)
            return run


def execute_render(
    engine: Engine, settings: AppConfig, plan: RenderPlan
) -> RenderResult:
    run = plan.run
    log_path = plan.workspace / "logs" / f"{run.public_id}.log"
    temporary = plan.workspace / "intermediate" / run.public_id
    output = plan.workspace / "output/audio" / f"{run.public_id}.m4a"
    published = False
    succeeded = False
    log_created = False
    try:
        try:
            for path in (log_path, temporary, output):
                safe_child(
                    plan.workspace,
                    path.relative_to(plan.workspace).as_posix(),
                )
            with open_render_log(log_path) as log:
                log_created = True
                log.write(f"{run.public_id}\n{run.ffmpeg_version}\n")
                with intermediate_directory(temporary):
                    candidate = render_pipeline(
                        plan.executable,
                        plan.music,
                        plan.ambience,
                        temporary,
                        plan.settings,
                        log,
                    )
                    stream, duration = verify_output(candidate, plan.settings)
                    for source, expected_hash in plan.source_hashes.items():
                        safe_child(
                            plan.workspace,
                            source.relative_to(plan.workspace).as_posix(),
                        )
                        if calculate_sha256(source) != expected_hash:
                            raise RenderError(
                                "Source changed during rendering: "
                                f"{source.name}"
                            )
                    output.parent.mkdir(exist_ok=True)
                    # Publish atomically without replacing an existing file.
                    os.link(candidate, output)
                    published = True
                    digest = calculate_sha256(output)
                    size = output.stat().st_size
            finished = record_success(
                engine,
                run.id,
                output.relative_to(settings.paths.data.resolve()).as_posix(),
                digest,
                size,
                stream,
                duration,
            )
            succeeded = True
            return RenderResult(finished, reused=False)
        finally:
            if published and not succeeded:
                output.unlink()
    except (
        RenderError,
        AssetError,
        MediaProbeError,
        OSError,
        SQLAlchemyError,
    ) as exc:
        message = str(exc)
        if log_created:
            try:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write(f"\nRender failed: {message}\n")
            except OSError as log_error:
                message += f"; cannot append to log: {log_error}"
        try:
            record_failure(engine, run.id, message)
        except SQLAlchemyError as database_error:
            raise RenderError(
                f"Render {run.public_id} failed: {message}. "
                f"Could not persist failure; inspect {log_path} "
                f"and database state: {database_error}"
            ) from database_error
        raise RenderError(
            f"Audio render failed. Render: {run.public_id}\n"
            f"Log: {log_path}\n{message}"
        ) from exc


def render_audio(
    engine: Engine, settings: AppConfig, public_id: str, *, force: bool = False
) -> RenderResult:
    try:
        plan = reserve_render(engine, settings, public_id, force)
        if isinstance(plan, RenderResult):
            return plan
        return execute_render(engine, settings, plan)
    except (AssetError, MediaProbeError, OSError, ValidationError) as exc:
        raise RenderError(str(exc)) from exc
