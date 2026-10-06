"""Immutable, verified JPEG artifacts derived solely from source artwork."""

import os
import tempfile
from pathlib import Path
from uuid import uuid4

from swale_sounds.assets.files import calculate_sha256, safe_child
from swale_sounds.assets.probe import probe_media
from swale_sounds.assets.service import compatible_stream
from swale_sounds.models import AssetKind
from swale_sounds.publishing.models import (
    PublicationError,
    SourceReference,
    ThumbnailReference,
    ThumbnailSettings,
)
from swale_sounds.rendering.ffmpeg import (
    find_ffmpeg,
    open_render_log,
    run_ffmpeg,
)
from swale_sounds.rendering.service import input_fingerprint


def thumbnail_fingerprint(
    source: SourceReference, settings: ThumbnailSettings, version: str
) -> str:
    return input_fingerprint(
        {"asset_id": source.asset_id, "sha256": source.sha256},
        settings.model_dump(mode="json"),
        version,
        settings.transform_version,
    )


def thumbnail_arguments(
    source: Path, output: Path, settings: ThumbnailSettings
) -> list[str]:
    return [
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"scale={settings.width}:{settings.height}:"
        f"force_original_aspect_ratio=increase:force_divisible_by=2:flags={settings.scale_flags},"
        f"crop={settings.width}:{settings.height},setsar=1",
        "-c:v",
        settings.codec,
        "-pix_fmt",
        settings.pixel_format,
        "-q:v",
        str(settings.quality),
        "-threads",
        str(settings.threads),
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:v",
        "+bitexact",
        "-f",
        "image2",
        "-update",
        "1",
        str(output),
    ]


def verify_thumbnail_media(path: Path, settings: ThumbnailSettings) -> int:
    if (
        not path.is_file()
        or not 0 < path.stat().st_size < settings.max_size_bytes
    ):
        raise PublicationError(
            "Thumbnail is missing, empty or exceeds the 2 MB limit."
        )
    media = probe_media(path, count_frames=True)
    if len(media.streams) != 1:
        raise PublicationError(
            "Thumbnail must contain exactly one still image."
        )
    stream = media.streams[0]
    if (
        stream.codec_name != "mjpeg"
        or stream.codec_type != "video"
        or stream.width != settings.width
        or stream.height != settings.height
        or stream.nb_read_frames != 1
        or stream.pix_fmt != settings.pixel_format
    ):
        raise PublicationError(
            "Thumbnail must be a 1280x720 yuvj420p JPEG still image."
        )
    return path.stat().st_size


def verify_thumbnail(
    workspace: Path,
    reference: ThumbnailReference,
    source: SourceReference,
    version: str,
    settings: ThumbnailSettings | None = None,
) -> Path:
    settings = settings or ThumbnailSettings()
    fingerprint = thumbnail_fingerprint(source, settings, version)
    expected_path = (
        f"output/publish/thumbnail-{fingerprint}-{reference.sha256}.jpg"
    )
    if (
        reference.source_asset_id != source.asset_id
        or reference.source_sha256 != source.sha256
        or reference.settings != settings
        or reference.ffmpeg_version != version
        or reference.fingerprint != fingerprint
        or reference.path != expected_path
    ):
        raise PublicationError(
            "Thumbnail provenance is stale or edited; "
            "rerun publish plan and review."
        )
    path = safe_child(workspace, reference.path)
    if not path.is_file() or calculate_sha256(path) != reference.sha256:
        raise PublicationError(
            "Thumbnail is missing or tampered; restore its recorded bytes "
            "or move the damaged file aside and rerun publish plan."
        )
    if verify_thumbnail_media(path, settings) != reference.size_bytes:
        raise PublicationError(
            "Thumbnail size differs from recorded provenance."
        )
    return path


def create_thumbnail(
    workspace: Path,
    source: SourceReference,
    previous: ThumbnailReference | None = None,
    settings: ThumbnailSettings | None = None,
) -> ThumbnailReference:
    settings = settings or ThumbnailSettings()
    artwork = safe_child(workspace, source.path)
    if not artwork.is_file() or calculate_sha256(artwork) != source.sha256:
        raise PublicationError(
            "Source artwork is missing or changed since import."
        )
    compatible_stream(
        probe_media(artwork, count_frames=True), AssetKind.ARTWORK, artwork
    )
    executable, version = find_ffmpeg()
    fingerprint = thumbnail_fingerprint(source, settings, version)
    if previous is not None and previous.fingerprint == fingerprint:
        old_path = safe_child(workspace, previous.path)
        if old_path.exists():
            verify_thumbnail(workspace, previous, source, version, settings)
            return previous
    directory = safe_child(workspace, "output/publish")
    directory.mkdir(exist_ok=True)
    log_path = safe_child(workspace, f"logs/thumbnail-{uuid4().hex}.log")
    try:
        with (
            open_render_log(log_path) as log,
            tempfile.TemporaryDirectory(
                dir=directory, prefix=".thumbnail-"
            ) as temp,
        ):
            candidate = Path(temp) / "thumbnail.jpg"
            run_ffmpeg(
                executable,
                thumbnail_arguments(artwork, candidate, settings),
                log,
            )
            size = verify_thumbnail_media(candidate, settings)
            digest = calculate_sha256(candidate)
            # Recheck the source before linking an immutable artifact.
            safe_child(workspace, source.path)
            if calculate_sha256(artwork) != source.sha256:
                raise PublicationError(
                    "Artwork changed during thumbnail generation."
                )
            relative = f"output/publish/thumbnail-{fingerprint}-{digest}.jpg"
            output = safe_child(workspace, relative)
            reference = ThumbnailReference(
                path=relative,
                sha256=digest,
                size_bytes=size,
                source_asset_id=source.asset_id,
                source_sha256=source.sha256,
                settings=settings,
                ffmpeg_version=version,
                fingerprint=fingerprint,
            )
            if output.exists():
                verify_thumbnail(
                    workspace, reference, source, version, settings
                )
            else:
                with candidate.open("rb") as stream:
                    os.fsync(stream.fileno())
                os.link(candidate, output)
            return reference
    except (OSError, ValueError) as exc:
        raise PublicationError(
            f"Thumbnail generation failed: {exc}. Log: {log_path}"
        ) from exc


def verify_derived_thumbnail(
    workspace: Path, reference: ThumbnailReference, source: SourceReference
) -> None:
    """Verify the transform without publishing or trusting edited hashes."""
    executable, version = find_ffmpeg()
    verify_thumbnail(workspace, reference, source, version)
    artwork = safe_child(workspace, source.path)
    if not artwork.is_file() or calculate_sha256(artwork) != source.sha256:
        raise PublicationError(
            "Thumbnail source artwork is missing or changed."
        )
    with tempfile.TemporaryDirectory(prefix="swale-thumbnail-check-") as temp:
        candidate = Path(temp) / "thumbnail.jpg"
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
            try:
                run_ffmpeg(
                    executable,
                    thumbnail_arguments(
                        artwork, candidate, reference.settings
                    ),
                    log,
                )
            except (OSError, ValueError) as exc:
                log.seek(0)
                raise PublicationError(
                    f"Cannot verify thumbnail transform: {exc}. "
                    f"Diagnostics: {log.read()[-2000:]}"
                ) from exc
        safe_child(workspace, source.path)
        if calculate_sha256(artwork) != source.sha256:
            raise PublicationError("Artwork changed during verification.")
        if calculate_sha256(candidate) != reference.sha256:
            raise PublicationError(
                "Thumbnail bytes do not match the recorded artwork transform; "
                "regenerate and review the publication package."
            )
