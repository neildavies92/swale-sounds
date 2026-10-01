"""Add audio execution history separately from immutable source assets."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "render_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.Enum(
                "audio",
                native_enum=False,
                create_constraint=True,
                name="render_stage",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "failed",
                native_enum=False,
                create_constraint=True,
                name="render_status",
            ),
            nullable=False,
        ),
        sa.Column("renderer_version", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("ffmpeg_version", sa.Text(), nullable=False),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("configuration_json", sa.JSON(), nullable=False),
        sa.Column("output_path", sa.Text(), unique=True),
        sa.Column("output_sha256", sa.Text()),
        sa.Column("output_size_bytes", sa.Integer()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("sample_rate", sa.Integer()),
        sa.Column("channels", sa.Integer()),
        sa.Column("codec_name", sa.Text()),
        sa.Column("log_path", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint(
            "renderer_version > 0", name="render_version_positive"
        ),
        sa.CheckConstraint(
            "output_size_bytes >= 0", name="render_size_nonnegative"
        ),
        sa.CheckConstraint(
            "duration_seconds >= 0", name="render_duration_nonnegative"
        ),
        sa.CheckConstraint("sample_rate > 0", name="render_rate_positive"),
        sa.CheckConstraint("channels > 0", name="render_channels_positive"),
    )
    op.create_index(
        "ix_render_reuse",
        "render_runs",
        ["session_id", "stage", "input_fingerprint", "status"],
    )
    op.create_index(
        "uq_running_render",
        "render_runs",
        ["session_id", "stage"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_running_render", table_name="render_runs")
    op.drop_index("ix_render_reuse", table_name="render_runs")
    op.drop_table("render_runs")
