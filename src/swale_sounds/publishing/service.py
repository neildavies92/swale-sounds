"""Derive and atomically write plans without changing production state."""

import os
import tempfile
from math import isclose
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as DatabaseSession

from swale_sounds.assets.files import safe_child
from swale_sounds.assets.probe import MediaProbeError
from swale_sounds.assets.provenance import AssetError
from swale_sounds.config import AppConfig
from swale_sounds.database import create_session_factory
from swale_sounds.models import RenderRun, RenderStage, RenderStatus, Session
from swale_sounds.publishing.metadata import (
    description_for,
    music_provenance,
    tags_for,
    title_for,
)
from swale_sounds.publishing.models import (
    PublicationError,
    PublicationPlan,
    SourceReference,
    VideoReference,
    plan_bytes,
)
from swale_sounds.rendering.ffmpeg import RenderError
from swale_sounds.rendering.service import (
    verified_render_output,
    verified_session_spec,
    verify_source,
)
from swale_sounds.rendering.video import (
    VIDEO_RENDERER_VERSION,
    parse_frame_rate,
    verify_video,
)
from swale_sounds.rendering.video_service import current_video_intent
from swale_sounds.sessions.service import SessionNotFoundError


def write_package(workspace: Path, plan: PublicationPlan) -> Path:
    path = safe_child(workspace, "output/publish/youtube.json")
    contents = plan_bytes(plan)
    previous: bytes | None = None
    if path.exists():
        if not path.is_file():
            raise PublicationError(
                "Publication manifest is not a regular file"
            )
        previous = path.read_bytes()
        try:
            old = PublicationPlan.model_validate_json(previous)
        except ValidationError as exc:
            raise PublicationError(
                "Existing youtube.json is not a recognized publication plan; "
                "move it aside before retrying."
            ) from exc
        if old.session_id != plan.session_id or plan_bytes(old) != previous:
            raise PublicationError(
                "Existing youtube.json belongs to another Session or has been "
                "edited; move it aside before retrying."
            )
        if previous == contents:
            return path
    path.parent.mkdir(exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".youtube-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        safe_child(workspace, "output/publish/youtube.json")
        if previous is None:
            # Atomic, exclusive first publication: never clobber another file.
            os.link(temporary, path)
        else:
            if not path.is_file() or path.read_bytes() != previous:
                raise PublicationError("Publication plan changed during write")
            os.replace(temporary, path)
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def derive_plan(
    db: DatabaseSession, settings: AppConfig, session: Session
) -> tuple[PublicationPlan, Path]:
    """Verify current authoritative inputs without writing a package."""
    public_id = session.public_id
    current = current_video_intent(db, session, settings)
    workspace = current.workspace
    spec = verified_session_spec(session, settings.paths.data, workspace)
    assets = [
        *current.audio_intent.music,
        *current.audio_intent.ambience,
        current.artwork_asset,
    ]
    sources = [
        SourceReference(
            asset_id=asset.public_id,
            path=verify_source(settings.paths.data, workspace, asset)
            .relative_to(workspace)
            .as_posix(),
            sha256=asset.sha256,
        )
        for asset in assets
    ]
    run = db.scalar(
        select(RenderRun)
        .where(
            RenderRun.session_id == session.id,
            RenderRun.stage == RenderStage.VIDEO,
            RenderRun.status == RenderStatus.SUCCEEDED,
            RenderRun.renderer_version == VIDEO_RENDERER_VERSION,
            RenderRun.input_fingerprint == current.fingerprint,
        )
        .order_by(RenderRun.id.desc())
    )
    if run is None:
        raise PublicationError(
            "No current successful video render. "
            f"Run 'swale-sounds render video {public_id}'."
        )
    output = verified_render_output(settings.paths.data, workspace, run)
    video, audio, duration = verify_video(
        output,
        current.settings,
        current.audio_intent.settings,
        current.duration,
    )
    if (
        run.inputs_json != current.inputs
        or run.configuration_json != current.configuration
        or run.ffmpeg_version != current.ffmpeg_version
        or run.output_size_bytes != output.stat().st_size
        or run.width != video.width
        or run.height != video.height
        or run.codec_name != video.codec_name
        or run.sample_rate != audio.sample_rate
        or run.channels != audio.channels
        or not isclose(run.duration_seconds or 0, duration)
        or not isclose(
            run.frame_rate or 0,
            parse_frame_rate(video.avg_frame_rate),
        )
    ):
        raise PublicationError(
            "Recorded video metadata differs from current output. "
            f"Run 'swale-sounds render video {public_id} --force'."
        )
    music = [music_provenance(asset) for asset in current.audio_intent.music]
    plan = PublicationPlan(
        session_id=session.public_id,
        spec_sha256=session.spec_sha256,
        audio_render_id=current.audio_run.public_id,
        video=VideoReference(
            render_id=run.public_id,
            path=output.relative_to(workspace).as_posix(),
            sha256=run.output_sha256 or "",
        ),
        source_assets=sources,
        artwork=sources[-1],
        title=title_for(spec),
        description=description_for(spec, music),
        tags=tags_for(spec),
        content=spec,
        music=music,
    )
    return plan, workspace


def create_publication_plan(
    engine: Engine, settings: AppConfig, public_id: str
) -> tuple[PublicationPlan, Path]:
    try:
        # Same serialization boundary as imports/renders/manifests. No writes
        # or commit: exiting rolls back the read transaction and releases it.
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            with create_session_factory(engine)(bind=connection) as db:
                session = db.scalar(
                    select(Session).where(Session.public_id == public_id)
                )
                if session is None:
                    raise SessionNotFoundError(
                        f"Session not found: {public_id}"
                    )
                plan, workspace = derive_plan(db, settings, session)
                return plan, write_package(workspace, plan)
    except (AssetError, MediaProbeError, RenderError) as exc:
        raise PublicationError(
            f"{exc} Verify source/specification integrity, then run "
            f"'swale-sounds render audio {public_id}' and "
            f"'swale-sounds render video {public_id} --force' as needed. "
            "If audio needs rebuilding after reaching video_rendered, "
            "create a new Session and import its sources: the current "
            "lifecycle does not allow audio rerendering in that status."
        ) from exc
    except (OSError, ValidationError) as exc:
        raise PublicationError(
            f"Cannot create publication plan: {exc}"
        ) from exc
