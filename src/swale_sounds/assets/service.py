"""Preflight, copy and persist each batch within one writer transaction."""

import mimetypes
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from swale_sounds.assets.files import (
    SOURCE_DIRECTORIES,
    calculate_sha256,
    copy_verified,
    discover_files,
    safe_child,
    safe_workspace,
)
from swale_sounds.assets.manifest import regenerate_manifest
from swale_sounds.assets.probe import (
    MediaInfo,
    MediaProbeError,
    MediaStream,
    probe_media,
)
from swale_sounds.assets.provenance import AssetError, Provenance
from swale_sounds.database import create_session_factory
from swale_sounds.models import Asset, AssetKind, Session, SessionStatus
from swale_sounds.sessions.service import SessionNotFoundError, get_session


@dataclass(frozen=True)
class Candidate:
    source: Path
    destination: Path
    sha256: str
    media: MediaInfo
    stream: MediaStream


@dataclass(frozen=True)
class ImportResult:
    imported: int
    skipped: list[str]
    manifest: Path


def compatible_stream(
    media: MediaInfo, kind: AssetKind, path: Path
) -> MediaStream:
    if kind != AssetKind.ARTWORK:
        for stream in media.streams:
            if stream.codec_type == "audio" and stream.codec_name:
                return stream
        raise AssetError(f"No audio stream found: {path.name}")
    codecs = {
        ".png": "png",
        ".jpg": "mjpeg",
        ".jpeg": "mjpeg",
        ".webp": "webp",
    }
    formats = {"image2", "png_pipe", "jpeg_pipe", "webp_pipe"}
    if len(media.streams) == 1:
        stream = media.streams[0]
        if (
            stream.codec_type == "video"
            and stream.codec_name == codecs[path.suffix.lower()]
            and media.format.format_name in formats
            and stream.nb_read_frames == 1
            and stream.width
            and stream.height
        ):
            return stream
    raise AssetError(f"Artwork must be a supported still image: {path.name}")


def preflight(
    source: Path,
    kind: AssetKind,
    workspace: Path,
    existing: list[Asset],
) -> tuple[list[Candidate], list[str]]:
    hashes = {asset.sha256: asset for asset in existing if asset.kind == kind}
    filenames = {
        Path(asset.path).name: asset
        for asset in existing
        if asset.kind == kind
    }
    candidates: list[Candidate] = []
    skipped: list[str] = []
    batch_hashes: set[str] = set()
    for path in discover_files(source, kind):
        digest = calculate_sha256(path)
        destination = safe_child(
            workspace, f"{SOURCE_DIRECTORIES[kind]}/{path.name}"
        )
        # Existing filenames take precedence over content deduplication.
        if destination.exists():
            registered = filenames.get(path.name)
            if (
                registered is None
                or registered.sha256 != digest
                or not destination.is_file()
                or calculate_sha256(destination) != digest
            ):
                raise AssetError(f"Asset filename conflict: {destination}")
        elif path.name in filenames:
            raise AssetError(f"Tracked source file is missing: {destination}")
        if digest in hashes:
            tracked = hashes[digest]
            # Verify duplicates rather than masking damaged or missing copies.
            stored = safe_child(
                workspace,
                f"{SOURCE_DIRECTORIES[kind]}/{tracked.original_filename}",
            )
            if not stored.is_file() or calculate_sha256(stored) != digest:
                raise AssetError(
                    f"Tracked source file is missing or changed: {stored}"
                )
            skipped.append(path.name)
            continue
        if digest in batch_hashes:
            skipped.append(path.name)
            continue
        media = probe_media(path, count_frames=kind == AssetKind.ARTWORK)
        stream = compatible_stream(media, kind, path)
        candidates.append(Candidate(path, destination, digest, media, stream))
        batch_hashes.add(digest)
    return candidates, skipped


def import_assets(
    engine: Engine,
    data_root: Path,
    public_id: str,
    source: Path,
    kind: AssetKind,
    provenance: Provenance,
    *,
    require_created_without_artwork: bool = False,
) -> ImportResult:
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
                workspace = safe_workspace(data_root, session.workspace_path)
                existing = list(
                    db.scalars(
                        select(Asset).where(Asset.session_id == session.id)
                    )
                )
                if require_created_without_artwork and (
                    kind != AssetKind.ARTWORK
                    or session.status != SessionStatus.CREATED
                    or any(
                        asset.kind == AssetKind.ARTWORK for asset in existing
                    )
                ):
                    raise AssetError(
                        "Import requires a created Session without artwork; "
                        "Session state changed before import."
                    )
                candidates, skipped = preflight(
                    source, kind, workspace, existing
                )
                # Revalidate supplied models before any filesystem mutation.
                provenance = Provenance.model_validate(provenance.model_dump())
                with ExitStack() as copies:
                    for candidate in candidates:
                        copies.enter_context(
                            copy_verified(
                                candidate.source,
                                candidate.destination,
                                candidate.sha256,
                            )
                        )
                        stream = candidate.stream
                        db.add(
                            Asset(
                                public_id=f"asset-{uuid4().hex}",
                                session_id=session.id,
                                kind=kind,
                                path=candidate.destination.relative_to(
                                    data_root.resolve()
                                ).as_posix(),
                                original_filename=candidate.source.name,
                                sha256=candidate.sha256,
                                size_bytes=candidate.destination.stat().st_size,
                                mime_type=mimetypes.guess_type(
                                    candidate.source.name
                                )[0],
                                format_name=candidate.media.format.format_name,
                                codec_name=stream.codec_name,
                                duration_seconds=(
                                    stream.duration
                                    if stream.duration is not None
                                    else candidate.media.format.duration
                                ),
                                sample_rate=stream.sample_rate,
                                channels=stream.channels,
                                width=stream.width,
                                height=stream.height,
                                **provenance.model_dump(),
                            )
                        )
                    kinds = {asset.kind for asset in existing}
                    if candidates:
                        kinds.add(kind)
                    if (
                        session.status == SessionStatus.CREATED
                        and {
                            AssetKind.MUSIC,
                            AssetKind.ARTWORK,
                        }
                        <= kinds
                    ):
                        session.status = SessionStatus.ASSETS_READY
                    db.flush()
                    committed = False
                    try:
                        connection.commit()
                        committed = True
                    finally:
                        if not committed:
                            connection.invalidate()
    except (OSError, MediaProbeError) as exc:
        raise AssetError(
            f"Cannot import assets into {public_id}: {exc}"
        ) from exc
    try:
        manifest = regenerate_manifest(engine, data_root, public_id)
    except (AssetError, SQLAlchemyError) as exc:
        raise AssetError(
            f"Assets committed for {public_id}, "
            f"but manifest generation failed: {exc}. "
            f"Run 'swale-sounds asset manifest {public_id}' to recover."
        ) from exc
    return ImportResult(len(candidates), skipped, manifest)


def list_assets(engine: Engine, public_id: str) -> list[Asset]:
    session = get_session(engine, public_id)
    with create_session_factory(engine)() as db:
        return list(
            db.scalars(
                select(Asset)
                .where(Asset.session_id == session.id)
                .order_by(
                    Asset.kind,
                    Asset.path,
                    Asset.public_id,
                )
            )
        )
