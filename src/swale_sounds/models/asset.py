"""Source asset identity, media metadata, and operator-supplied provenance."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from swale_sounds.database import Base
from swale_sounds.models.session import UTCDateTime, utc_now

if TYPE_CHECKING:
    from swale_sounds.models.session import Session


class AssetKind(StrEnum):
    MUSIC = "music_source"
    ARTWORK = "artwork_source"
    AMBIENCE = "ambience_source"


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "kind", "sha256", name="uq_asset_content"
        ),
        UniqueConstraint("path", name="uq_asset_path"),
        CheckConstraint("size_bytes >= 0", name="asset_size_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, unique=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT"), index=True
    )
    session: Mapped["Session"] = relationship(back_populates="assets")
    kind: Mapped[AssetKind] = mapped_column(
        Enum(
            AssetKind,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
            name="asset_kind",
        )
    )
    path: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int]
    mime_type: Mapped[str | None] = mapped_column(Text)
    format_name: Mapped[str | None] = mapped_column(Text)
    codec_name: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    sample_rate: Mapped[int | None]
    channels: Mapped[int | None]
    width: Mapped[int | None]
    height: Mapped[int | None]
    provider: Mapped[str] = mapped_column(Text, default="manual")
    provider_model: Mapped[str | None] = mapped_column(Text)
    provider_plan: Mapped[str | None] = mapped_column(Text)
    generation_prompt: Mapped[str | None] = mapped_column(Text)
    generation_parameters: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    licence_notes: Mapped[str | None] = mapped_column(Text)
    licence_url: Mapped[str | None] = mapped_column(Text)
    licence_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now
    )
