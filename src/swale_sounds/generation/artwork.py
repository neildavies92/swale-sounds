"""Deterministic artwork intent and handoff to the normal Asset importer."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from swale_sounds.assets.files import safe_child, safe_workspace
from swale_sounds.assets.provenance import AssetError, Provenance
from swale_sounds.assets.service import import_assets, list_assets
from swale_sounds.config import AppConfig, ArtworkGenerationConfig
from swale_sounds.generation.openai_images import (
    GenerationError,
    generate_image,
    request_parameters,
    validate_prompt,
)
from swale_sounds.models import Asset, AssetKind, Session, SessionStatus
from swale_sounds.rendering.ffmpeg import RenderError
from swale_sounds.rendering.service import verified_session_spec
from swale_sounds.sessions.schema import SessionSpec
from swale_sounds.sessions.service import SessionNotFoundError, get_session


@dataclass(frozen=True)
class ArtworkPlan:
    session: Session
    workspace: Path
    prompt: str
    settings: ArtworkGenerationConfig


@dataclass(frozen=True)
class ArtworkResult:
    asset: Asset
    manifest: Path


def build_prompt(spec: SessionSpec) -> str:
    lines = [
        "Create a high-quality cinematic still image for a long-form "
        "music and ambience video.",
        "",
        f"Scene: {spec.session.title}.",
    ]
    for label, values in (
        ("Atmosphere", spec.music.mood),
        ("Purpose", spec.context.purpose),
        ("Environment", spec.context.environment),
        ("Location", spec.context.location),
        ("Weather", spec.context.weather),
        ("Time", spec.context.time),
        ("Season", spec.context.season),
        ("Visual style", [spec.visual.style] if spec.visual.style else []),
        ("Still-image visual motifs", spec.visual.animation),
    ):
        if values:
            text = ", ".join(value.replace("_", " ") for value in values)
            lines.append(f"{label}: {text}.")
    lines += [
        "",
        "Composition: calm, immersive, cohesive and visually detailed; "
        "suitable as a static background for an extended focus "
        "or relaxation video.",
        "Create one still image, not a video or animated image.",
        "Do not include text, titles, captions, logos, watermarks, "
        "UI or interface elements, borders, or readable signage.",
    ]
    return validate_prompt("\n".join(lines) + "\n")


def preflight_session(
    engine: Engine, settings: AppConfig, public_id: str
) -> tuple[Session, Path, SessionSpec]:
    session = get_session(engine, public_id)
    if session.status != SessionStatus.CREATED:
        raise GenerationError(
            f"Session {public_id} is not ready for artwork generation; "
            f"status is {session.status.value}. Expected created."
        )
    if any(
        asset.kind == AssetKind.ARTWORK
        for asset in list_assets(engine, public_id)
    ):
        raise GenerationError(
            f"Session {public_id} already has an artwork source. "
            "Milestone 6 does not replace or add artwork automatically."
        )
    workspace = safe_workspace(settings.paths.data, session.workspace_path)
    spec = verified_session_spec(session, settings.paths.data, workspace)
    return session, workspace, spec


def prepare_artwork(
    engine: Engine,
    settings: AppConfig,
    public_id: str,
    prompt_file: Path | None = None,
) -> ArtworkPlan:
    try:
        session, workspace, spec = preflight_session(
            engine, settings, public_id
        )
        artwork_settings = ArtworkGenerationConfig.model_validate(
            settings.generation.artwork.model_dump()
        )
        if prompt_file is None:
            prompt = build_prompt(spec)
        else:
            # Preserve exact UTF-8 content, including CRLF and trailing spaces.
            with prompt_file.open(encoding="utf-8", newline="") as stream:
                prompt = validate_prompt(stream.read())
        destination = safe_child(
            workspace,
            f"artwork/source/generated-artwork.{artwork_settings.output_format}",
        )
        if not destination.parent.is_dir():
            raise GenerationError("Artwork source directory is missing.")
        if destination.exists():
            raise GenerationError(
                f"Artwork destination already exists: {destination}"
            )
        return ArtworkPlan(session, workspace, prompt, artwork_settings)
    except (
        AssetError,
        RenderError,
        OSError,
        UnicodeError,
        ValidationError,
    ) as exc:
        raise GenerationError(
            f"Cannot prepare artwork generation: {exc}"
        ) from exc


def generate_artwork(
    engine: Engine,
    settings: AppConfig,
    public_id: str,
    *,
    prompt_file: Path | None = None,
    licence_notes: str | None = None,
    licence_url: str | None = None,
    licence_version: str | None = None,
) -> ArtworkResult:
    plan = prepare_artwork(engine, settings, public_id, prompt_file)
    provenance = Provenance(
        provider="openai",
        provider_model=plan.settings.model,
        generation_prompt=plan.prompt,
        generation_parameters={"request": request_parameters(plan.settings)},
        licence_notes=licence_notes,
        licence_url=licence_url,
        licence_version=licence_version,
    )
    if shutil.which("ffprobe") is None:
        raise GenerationError(
            "ffprobe is required to import generated artwork; "
            "install FFmpeg first."
        )
    try:
        staging = safe_child(
            plan.workspace, f"intermediate/generation-{uuid4().hex}"
        )
        staging.mkdir()
    except (AssetError, OSError) as exc:
        raise GenerationError(
            f"Cannot create artwork staging directory: {exc}"
        ) from exc
    try:
        image = generate_image(plan.prompt, plan.settings)
    except (GenerationError, OSError) as exc:
        # No decoded paid result exists yet. Remove only our empty directory.
        try:
            staging.rmdir()
        except OSError as cleanup_error:
            raise GenerationError(
                f"{exc}; could not remove empty staging {staging}: "
                f"{cleanup_error}"
            ) from None
        raise GenerationError(str(exc)) from None
    extension = (
        image.output_format
        if image.output_format in {"png", "jpeg", "webp"}
        else "bin"
    )
    candidate = staging / f"generated-artwork.{extension}"
    try:
        safe_child(
            plan.workspace, candidate.relative_to(plan.workspace).as_posix()
        )
        with candidate.open("xb") as stream:
            stream.write(image.contents)
    except (AssetError, OSError) as exc:
        raise GenerationError(
            "Artwork was generated but could not be staged completely at "
            f"{candidate}. Inspect staging before retrying. Reason: {exc}"
        ) from exc
    try:
        # A concurrent operator may have changed state during the paid request.
        current, workspace, _ = preflight_session(engine, settings, public_id)
        if (
            workspace != plan.workspace
            or current.spec_sha256 != plan.session.spec_sha256
        ):
            raise GenerationError(
                "Session specification changed during generation."
            )
        provenance.generation_parameters = {
            "request": request_parameters(plan.settings),
            "response": image.metadata,
        }
        result = import_assets(
            engine,
            settings.paths.data,
            public_id,
            candidate,
            AssetKind.ARTWORK,
            provenance,
            require_created_without_artwork=True,
        )
        asset = next(
            asset
            for asset in list_assets(engine, public_id)
            if asset.kind == AssetKind.ARTWORK
            and asset.path
            == (plan.workspace / "artwork/source" / candidate.name)
            .relative_to(settings.paths.data.resolve())
            .as_posix()
        )
    except (
        AssetError,
        GenerationError,
        RenderError,
        OSError,
        ValidationError,
        SQLAlchemyError,
        SessionNotFoundError,
    ) as exc:
        raise GenerationError(
            "Artwork was generated but could not be imported completely.\n"
            f"Generated candidate retained at: {candidate}\nReason: {exc}"
        ) from exc
    try:
        candidate.unlink()
        staging.rmdir()
    except OSError as exc:
        raise GenerationError(
            f"Artwork Asset {asset.public_id} was imported, "
            f"but staging cleanup failed at {staging}: {exc}"
        ) from exc
    return ArtworkResult(asset, result.manifest)
