"""Deterministic, deliberately small metadata rules; no inferred rights."""

import re
import unicodedata

from swale_sounds.models import Asset
from swale_sounds.publishing.models import (
    MusicProvenance,
    PublicationError,
    tags_length,
)
from swale_sounds.sessions.schema import SessionSpec


def readable(value: str) -> str:
    return " ".join(value.replace("_", " ").split())


def title_for(spec: SessionSpec) -> str:
    scene = readable(spec.session.title).title()
    music = " ".join(
        readable(value).title()
        for value in spec.music.mood[:1] + spec.music.genre[:1]
    )
    purpose = " & ".join(
        readable(value).title() for value in spec.context.purpose[:2]
    )
    detail = music + (f" for {purpose}" if purpose else "")
    title = " | ".join(part for part in (scene, detail.strip()) if part)
    title = title.replace("<", "").replace(">", "").strip() or "Untitled"
    if len(title) > 100:
        title = title[:99].rstrip()
        # Avoid leaving a split combining sequence at the truncation boundary.
        while title and unicodedata.combining(title[-1]):
            title = title[:-1]
        title += "…"
    return title


def tags_for(spec: SessionSpec) -> list[str]:
    dimensions = [spec.music.genre, spec.music.mood]
    dimensions.extend(
        getattr(spec.context, field)
        for field in type(spec.context).model_fields
    )
    tags: list[str] = []
    for values in dimensions:
        for value in values:
            tag = readable(value).replace("<", "").replace(">", "").strip()
            if tag and tag not in tags and tags_length([*tags, tag]) <= 500:
                tags.append(tag)
    return tags


def music_provenance(asset: Asset) -> MusicProvenance:
    notes = asset.licence_notes or ""
    # Explicit operator convention, independent of provider or licence name.
    declarations = []
    for field in re.split(r"[;\n]", notes):
        declaration = " ".join(field.lower().strip().rstrip(".").split())
        if declaration in {
            "attribution: not required",
            "attribution not required",
            "attribution is not required",
            "no attribution required",
        }:
            declarations.append("not required")
        elif declaration in {
            "attribution: required",
            "attribution required",
            "attribution is required",
            "requires attribution",
        }:
            declarations.append("required")
    text_match = re.search(
        r"(?:^|\n)[ \t]*attribution text:[ \t]*([^\r\n]+)",
        notes,
        re.IGNORECASE,
    )
    text = text_match.group(1).strip() if text_match else None
    states = {value.lower() for value in declarations}
    if len(states) > 1 or (notes.strip() and not states):
        raise PublicationError(
            f"Music Asset {asset.public_id}: ambiguous attribution evidence. "
            "Record 'Attribution: required' or 'Attribution: not required' "
            "in licence_notes; required credits need a separate "
            "'Attribution text: <exact credit>' line."
        )
    required = states == {"required"}
    if required and not text:
        raise PublicationError(
            f"Music Asset {asset.public_id} requires attribution but its "
            "credit is missing. Record the exact credit in licence_notes "
            "on a separate 'Attribution text: <exact credit>' line."
        )
    return MusicProvenance(
        asset_id=asset.public_id,
        original_filename=asset.original_filename,
        provider=asset.provider,
        provider_model=asset.provider_model,
        licence_notes=asset.licence_notes,
        licence_url=asset.licence_url,
        licence_version=asset.licence_version,
        attribution=(
            "required" if required else "not_required" if states else "unknown"
        ),
        attribution_text=text,
    )


def description_for(spec: SessionSpec, music: list[MusicProvenance]) -> str:
    lines = [readable(spec.session.title), ""]
    for label, values in [
        ("Purpose", spec.context.purpose),
        ("Genre", spec.music.genre),
        ("Mood", spec.music.mood),
        ("Instruments", spec.music.instruments),
        *[
            (field.capitalize(), getattr(spec.context, field))
            for field in type(spec.context).model_fields
            if field != "purpose"
        ],
    ]:
        if values:
            lines.append(f"{label}: {', '.join(map(readable, values))}")
    if spec.music.bpm:
        lines.append(f"BPM: {spec.music.bpm.min}-{spec.music.bpm.max}")
    lines += [
        f"Duration: {spec.output.duration_minutes} minutes",
        "",
        "Music / licence evidence (operator supplied):",
    ]
    for item in music:
        lines.append(f"{item.original_filename} [{item.asset_id}]")
        lines.append(f"Provider: {item.provider}")
        if item.licence_notes:
            lines.append(item.licence_notes)
        if item.licence_url:
            lines.append(f"Licence URL: {item.licence_url}")
        if item.licence_version:
            lines.append(f"Licence version: {item.licence_version}")
        if item.attribution == "unknown":
            lines.append(
                "Attribution requirements not recorded; review needed."
            )
        lines.append("")
    description = "\n".join(lines).rstrip()
    if "<" in description or ">" in description:
        raise PublicationError(
            "Description evidence contains angle brackets unsupported by "
            "YouTube; correct the recorded text before planning."
        )
    if len(description.encode("utf-8")) > 5000:
        raise PublicationError(
            "Description exceeds YouTube's 5000-byte limit. Shorten the "
            "recorded metadata/licence notes without omitting credits."
        )
    return description
