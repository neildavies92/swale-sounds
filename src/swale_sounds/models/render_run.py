"""Execution history and immutable derived-output provenance."""

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
    Index,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from swale_sounds.database import Base
from swale_sounds.models.session import UTCDateTime, utc_now

if TYPE_CHECKING:
    from swale_sounds.models.session import Session


class RenderStage(StrEnum):
    AUDIO = "audio"


class RenderStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RenderRun(Base):
    __tablename__ = "render_runs"
    __table_args__ = (
        Index(
            "ix_render_reuse",
            "session_id",
            "stage",
            "input_fingerprint",
            "status",
        ),
        Index(
            "uq_running_render",
            "session_id",
            "stage",
            unique=True,
            sqlite_where=text("status = 'running'"),
        ),
        CheckConstraint(
            "renderer_version > 0", name="render_version_positive"
        ),
        CheckConstraint(
            "output_size_bytes >= 0", name="render_size_nonnegative"
        ),
        CheckConstraint(
            "duration_seconds >= 0", name="render_duration_nonnegative"
        ),
        CheckConstraint("sample_rate > 0", name="render_rate_positive"),
        CheckConstraint("channels > 0", name="render_channels_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, unique=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT")
    )
    session: Mapped["Session"] = relationship(back_populates="render_runs")
    stage: Mapped[RenderStage] = mapped_column(
        Enum(
            RenderStage,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="render_stage",
        )
    )
    status: Mapped[RenderStatus] = mapped_column(
        Enum(
            RenderStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="render_status",
        )
    )
    renderer_version: Mapped[int]
    input_fingerprint: Mapped[str] = mapped_column(Text)
    ffmpeg_version: Mapped[str] = mapped_column(Text)
    inputs_json: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    configuration_json: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    output_path: Mapped[str | None] = mapped_column(Text, unique=True)
    output_sha256: Mapped[str | None] = mapped_column(Text)
    output_size_bytes: Mapped[int | None]
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    sample_rate: Mapped[int | None]
    channels: Mapped[int | None]
    codec_name: Mapped[str | None] = mapped_column(Text)
    log_path: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_message: Mapped[str | None] = mapped_column(Text)
