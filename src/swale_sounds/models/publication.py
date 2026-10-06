"""Historical manual publication facts and exact plan snapshots."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from swale_sounds.database import Base
from swale_sounds.models.session import UTCDateTime, utc_now

if TYPE_CHECKING:
    from swale_sounds.models.render_run import RenderRun
    from swale_sounds.models.session import Session


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint(
            "platform", "external_id", name="uq_publication_external"
        ),
        CheckConstraint("platform = 'youtube'", name="publication_platform"),
        CheckConstraint("status = 'published'", name="publication_status"),
        CheckConstraint(
            "length(trim(external_id)) > 0",
            name="publication_external_nonempty",
        ),
        CheckConstraint("plan_version > 0", name="publication_plan_version"),
        CheckConstraint(
            "length(plan_sha256) = 64 AND plan_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="publication_digest",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, unique=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT"), index=True
    )
    video_render_id: Mapped[int] = mapped_column(
        ForeignKey("render_runs.id", ondelete="RESTRICT"), index=True
    )
    session: Mapped["Session"] = relationship()
    video_render: Mapped["RenderRun"] = relationship()
    platform: Mapped[str] = mapped_column(Text, default="youtube")
    external_id: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="published")
    published_at: Mapped[datetime] = mapped_column(UTCDateTime())
    plan_version: Mapped[int]
    plan_sha256: Mapped[str] = mapped_column(Text)
    plan_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now
    )
