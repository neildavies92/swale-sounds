"""Minimal session persistence, without lifecycle or allocation logic."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import JsonValue
from sqlalchemy import JSON, DateTime, Enum, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from swale_sounds.database import Base

if TYPE_CHECKING:
    from swale_sounds.models.asset import Asset


class SessionStatus(StrEnum):
    CREATED = "created"
    ASSETS_READY = "assets_ready"
    AUDIO_RENDERED = "audio_rendered"
    VIDEO_RENDERED = "video_rendered"
    FAILED = "failed"


class UTCDateTime(TypeDecorator[datetime]):
    """Store naive UTC in SQLite and restore aware UTC on read."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("Timestamps must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


def utc_now() -> datetime:
    return datetime.now(UTC)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    assets: Mapped[list["Asset"]] = relationship(
        back_populates="session", passive_deletes="all"
    )
    public_id: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(
            SessionStatus,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
            name="session_status",
        ),
        default=SessionStatus.CREATED,
    )
    spec_version: Mapped[int]
    spec_path: Mapped[str] = mapped_column(Text)
    spec_sha256: Mapped[str] = mapped_column(Text)
    spec_json: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    workspace_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now
    )
