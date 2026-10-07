"""Record operator-attested uploads without contacting YouTube."""

import re
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import joinedload

from swale_sounds.assets.files import safe_child, safe_workspace
from swale_sounds.assets.probe import MediaProbeError
from swale_sounds.assets.provenance import AssetError
from swale_sounds.config import AppConfig
from swale_sounds.database import create_session_factory
from swale_sounds.models import Publication, RenderRun, Session
from swale_sounds.models.session import utc_now
from swale_sounds.publishing.models import (
    PublicationError,
    PublicationPlanV2,
    parse_plan,
    plan_bytes,
    plan_with_thumbnail,
)
from swale_sounds.publishing.service import derive_plan
from swale_sounds.publishing.thumbnail import verify_derived_thumbnail
from swale_sounds.rendering.ffmpeg import RenderError
from swale_sounds.sessions.schema import sha256_bytes
from swale_sounds.sessions.service import SessionNotFoundError


def publication_time(value: str | None) -> datetime:
    if value is None:
        return utc_now()
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError("timezone required")
        return result.astimezone(UTC)
    except ValueError as exc:
        raise PublicationError(
            "Publication time must be ISO-8601 with a timezone."
        ) from exc


def record_publication(
    engine: Engine,
    settings: AppConfig,
    public_id: str,
    video_id: str,
    published_at: str | None = None,
) -> tuple[Publication, bool]:
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) is None:
        raise PublicationError(
            "YouTube video ID must be 11 letters, digits, '_' or '-'."
        )
    timestamp = publication_time(published_at)
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
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
                workspace = safe_workspace(
                    settings.paths.data, session.workspace_path
                )
                path = safe_child(workspace, "output/publish/youtube.json")
                if not path.is_file():
                    raise PublicationError(
                        "Publication plan is missing or not a regular file."
                    )
                payload = path.read_bytes()
                selected = parse_plan(payload)
                digest = sha256_bytes(payload)
                if (
                    selected.session_id != public_id
                    or plan_bytes(selected) != payload
                ):
                    raise PublicationError(
                        "Publication plan was edited "
                        "or belongs to another Session."
                    )
                expected, _ = derive_plan(db, settings, session)
                if isinstance(selected, PublicationPlanV2):
                    verify_derived_thumbnail(
                        workspace,
                        selected.thumbnail,
                        expected.artwork,
                    )
                    expected_bytes = plan_bytes(
                        plan_with_thumbnail(expected, selected.thumbnail)
                    )
                else:
                    expected_bytes = plan_bytes(expected)
                if sha256_bytes(expected_bytes) != digest:
                    raise PublicationError(
                        "Publication plan is stale or edited; "
                        "it differs from current verified intent."
                    )
                run = db.scalar(
                    select(RenderRun).where(
                        RenderRun.public_id == selected.video.render_id
                    )
                )
                if run is None:
                    raise PublicationError(
                        "Referenced video RenderRun is missing."
                    )
                existing = db.scalar(
                    select(Publication).where(
                        Publication.platform == "youtube",
                        Publication.external_id == video_id,
                    )
                )
                if existing is not None:
                    if (
                        existing.session_id != session.id
                        or existing.video_render_id != run.id
                        or existing.plan_sha256 != digest
                        or existing.canonical_url != url
                    ):
                        raise PublicationError(
                            "External video ID already "
                            "records conflicting provenance; "
                            "check the ID and original "
                            "Publication. History was not changed."
                        )
                    db.expunge(existing)
                    return existing, True
                row = Publication(
                    public_id=f"publication-{uuid4().hex}",
                    session_id=session.id,
                    video_render_id=run.id,
                    platform="youtube",
                    external_id=video_id,
                    canonical_url=url,
                    status="published",
                    published_at=timestamp,
                    plan_version=selected.plan_version,
                    plan_sha256=digest,
                    plan_text=payload.decode("utf-8"),
                )
                db.add(row)
                db.flush()
                db.expunge(row)
                connection.commit()
                return row, False
    except (
        OSError,
        ValidationError,
        AssetError,
        MediaProbeError,
        RenderError,
        PublicationError,
    ) as exc:
        raise PublicationError(
            f"Cannot record publication: {exc} "
            f"Run 'swale-sounds publish plan {public_id}' only after "
            "repairing inputs, then review the "
            "package against the actual upload. "
            "The selected plan was not regenerated."
        ) from exc


def list_publications(
    engine: Engine, public_id: str | None = None
) -> list[Publication]:
    with create_session_factory(engine)() as db:
        query = (
            select(Publication)
            .options(
                joinedload(Publication.session),
                joinedload(Publication.video_render),
            )
            .order_by(Publication.id)
        )
        if public_id is not None:
            query = query.where(Publication.public_id == public_id)
        return list(db.scalars(query))
