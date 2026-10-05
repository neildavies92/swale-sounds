"""Resolve current audio provenance and reserve immutable video attempts."""

from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from pydantic import JsonValue, ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as DatabaseSession

from swale_sounds.assets.files import safe_workspace
from swale_sounds.assets.probe import MediaProbeError, MediaStream, probe_media
from swale_sounds.assets.provenance import AssetError
from swale_sounds.assets.service import compatible_stream
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
from swale_sounds.rendering.audio import (
    AUDIO_RENDERER_VERSION,
    AudioSettings,
    verify_output,
)
from swale_sounds.rendering.ffmpeg import RenderError, find_ffmpeg
from swale_sounds.rendering.service import (
    AudioIntent,
    RenderResult,
    asset_snapshot,
    commit_or_discard,
    current_audio_intent,
    execute_attempt,
    input_fingerprint,
    verified_render_output,
    verify_source,
)
from swale_sounds.rendering.video import (
    VIDEO_RENDERER_VERSION,
    VideoSettings,
    render_video_pipeline,
    verify_video,
)
from swale_sounds.sessions.service import SessionNotFoundError


def audio_is_current(candidate: RenderRun, intent: AudioIntent) -> bool:
    return (
        candidate.stage == RenderStage.AUDIO
        and candidate.status == RenderStatus.SUCCEEDED
        and candidate.renderer_version == AUDIO_RENDERER_VERSION
        and candidate.input_fingerprint
        == input_fingerprint(
            intent.inputs,
            intent.settings.model_dump(mode="json"),
            candidate.ffmpeg_version,
            AUDIO_RENDERER_VERSION,
        )
    )


@dataclass(frozen=True)
class VideoPlan:
    run: RenderRun
    workspace: Path
    executable: str
    artwork: Path
    audio: Path
    audio_settings: AudioSettings
    audio_duration: float
    settings: VideoSettings
    source_hashes: dict[Path, str]


@dataclass(frozen=True)
class VideoIntent:
    workspace: Path
    audio_intent: AudioIntent
    audio_run: RenderRun
    artwork_asset: Asset
    audio: Path
    artwork: Path
    duration: float
    executable: str
    settings: VideoSettings
    inputs: dict[str, JsonValue]
    configuration: dict[str, JsonValue]
    fingerprint: str
    ffmpeg_version: str


def current_video_intent(
    db: DatabaseSession, session: Session, settings: AppConfig
) -> VideoIntent:
    """Resolve shared current inputs for rendering and publication planning."""
    workspace = safe_workspace(settings.paths.data, session.workspace_path)
    video_settings = VideoSettings(**settings.media.video.model_dump())
    intent = current_audio_intent(db, session, settings, workspace)
    candidates = db.scalars(
        select(RenderRun)
        .where(
            RenderRun.session_id == session.id,
            RenderRun.stage == RenderStage.AUDIO,
            RenderRun.status == RenderStatus.SUCCEEDED,
        )
        .order_by(RenderRun.id.desc())
    )
    audio_run = next(
        (run for run in candidates if audio_is_current(run, intent)),
        None,
    )
    if audio_run is None:
        raise RenderError(
            "No current successful audio render exists for "
            f"{session.public_id}. "
            f"Run 'swale-sounds render audio {session.public_id}' first."
        )
    try:
        audio = verified_render_output(
            settings.paths.data, workspace, audio_run
        )
        _, duration = verify_output(audio, intent.settings)
    except (RenderError, MediaProbeError) as exc:
        raise RenderError(
            "Recorded audio render provenance no longer matches "
            f"disk state: {audio_run.public_id}. "
            f"Run 'swale-sounds render audio {session.public_id} --force' "
            f"to repair it. {exc}"
        ) from exc
    artworks = list(
        db.scalars(
            select(Asset).where(
                Asset.session_id == session.id,
                Asset.kind == AssetKind.ARTWORK,
            )
        )
    )
    if len(artworks) != 1:
        raise RenderError(
            "Video rendering requires exactly one artwork source; "
            f"found {len(artworks)}."
        )
    artwork_asset = artworks[0]
    artwork = verify_source(settings.paths.data, workspace, artwork_asset)
    compatible_stream(
        probe_media(artwork, count_frames=True),
        AssetKind.ARTWORK,
        artwork,
    )
    executable, version = find_ffmpeg()
    inputs: dict[str, JsonValue] = {
        "spec_sha256": session.spec_sha256,
        "audio_render": {
            "render_id": audio_run.public_id,
            "output_path": audio_run.output_path,
            "sha256": audio_run.output_sha256,
            "input_fingerprint": audio_run.input_fingerprint,
        },
        "artwork": asset_snapshot(artwork_asset),
    }
    configuration = video_settings.model_dump(mode="json")
    fingerprint = input_fingerprint(
        inputs, configuration, version, VIDEO_RENDERER_VERSION
    )
    return VideoIntent(
        workspace,
        intent,
        audio_run,
        artwork_asset,
        audio,
        artwork,
        duration,
        executable,
        video_settings,
        inputs,
        configuration,
        fingerprint,
        version,
    )


def reserve_video(
    engine: Engine, settings: AppConfig, public_id: str, force: bool
) -> VideoPlan | RenderResult:
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        with create_session_factory(engine)(bind=connection) as db:
            session = db.scalar(
                select(Session).where(Session.public_id == public_id)
            )
            if session is None:
                raise SessionNotFoundError(f"Session not found: {public_id}")
            if session.status not in {
                SessionStatus.AUDIO_RENDERED,
                SessionStatus.VIDEO_RENDERED,
            }:
                raise RenderError(
                    f"Session {public_id} is not ready for video rendering; "
                    f"status is {session.status.value}."
                )
            active = db.scalar(
                select(RenderRun).where(
                    RenderRun.session_id == session.id,
                    RenderRun.stage == RenderStage.VIDEO,
                    RenderRun.status == RenderStatus.RUNNING,
                )
            )
            if active is not None:
                raise RenderError(
                    f"Video render already running: {active.public_id}. "
                    "Inspect its log before retrying."
                )
            current = current_video_intent(db, session, settings)
            workspace = current.workspace
            intent = current.audio_intent
            audio_run = current.audio_run
            artwork_asset = current.artwork_asset
            audio, artwork = current.audio, current.artwork
            duration = current.duration
            executable, version = current.executable, current.ffmpeg_version
            video_settings = current.settings
            inputs, configuration = current.inputs, current.configuration
            fingerprint = current.fingerprint
            if not force:
                existing = db.scalar(
                    select(RenderRun)
                    .where(
                        RenderRun.session_id == session.id,
                        RenderRun.stage == RenderStage.VIDEO,
                        RenderRun.status == RenderStatus.SUCCEEDED,
                        RenderRun.input_fingerprint == fingerprint,
                    )
                    .order_by(RenderRun.id.desc())
                )
                if existing is not None:
                    verified_render_output(
                        settings.paths.data, workspace, existing
                    )
                    db.expunge(existing)
                    return RenderResult(existing, reused=True)
            render_id = f"render-{uuid4().hex}"
            run = RenderRun(
                public_id=render_id,
                session_id=session.id,
                stage=RenderStage.VIDEO,
                status=RenderStatus.RUNNING,
                renderer_version=VIDEO_RENDERER_VERSION,
                input_fingerprint=fingerprint,
                ffmpeg_version=version,
                inputs_json=inputs,
                configuration_json=configuration,
                log_path=f"{session.workspace_path}/logs/{render_id}.log",
            )
            db.add(run)
            db.flush()
            db.expunge(run)
            # Capture expired ORM values before closing the reservation.
            hashes = {
                artwork: artwork_asset.sha256,
                audio: audio_run.output_sha256 or "",
            }
            commit_or_discard(connection)
            return VideoPlan(
                run,
                workspace,
                executable,
                artwork,
                audio,
                intent.settings,
                duration,
                video_settings,
                hashes,
            )


def render_video(
    engine: Engine, settings: AppConfig, public_id: str, *, force: bool = False
) -> RenderResult:
    try:
        plan = reserve_video(engine, settings, public_id, force)
        if isinstance(plan, RenderResult):
            return plan

        def candidate(
            directory: Path, log: TextIO
        ) -> tuple[Path, MediaStream, float, MediaStream | None]:
            path = render_video_pipeline(
                plan.executable,
                plan.artwork,
                plan.audio,
                directory,
                plan.settings,
                plan.audio_duration,
                log,
            )
            video, audio, duration = verify_video(
                path, plan.settings, plan.audio_settings, plan.audio_duration
            )
            return path, audio, duration, video

        return execute_attempt(
            engine,
            settings,
            plan.run,
            plan.workspace,
            plan.source_hashes,
            "mp4",
            candidate,
        )
    except (AssetError, MediaProbeError, OSError, ValidationError) as exc:
        raise RenderError(str(exc)) from exc
