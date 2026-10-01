"""Extend render history to video without discarding audio provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("render_runs", recreate="always") as batch:
        batch.drop_constraint("render_stage", type_="check")
        batch.alter_column(
            "stage", existing_type=sa.String(5), type_=sa.String(5)
        )
        batch.create_check_constraint(
            "render_stage", "stage IN ('audio', 'video')"
        )
        batch.add_column(sa.Column("width", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("height", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("frame_rate", sa.Float(), nullable=True))
        batch.create_check_constraint("render_width_positive", "width > 0")
        batch.create_check_constraint("render_height_positive", "height > 0")
        batch.create_check_constraint("render_fps_positive", "frame_rate > 0")


def downgrade() -> None:
    # An audio-only schema cannot represent video history. Refuse data loss.
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM render_runs WHERE stage = 'video'")
    ):
        raise RuntimeError(
            "Cannot downgrade while video RenderRuns exist; "
            "preserve their history."
        )
    with op.batch_alter_table("render_runs", recreate="always") as batch:
        batch.drop_constraint("render_stage", type_="check")
        batch.create_check_constraint("render_stage", "stage IN ('audio')")
        for name in (
            "render_width_positive",
            "render_height_positive",
            "render_fps_positive",
        ):
            batch.drop_constraint(name, type_="check")
        for name in ("width", "height", "frame_rate"):
            batch.drop_column(name)
