"""Create the initial sessions table."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "created",
                "assets_ready",
                "audio_rendered",
                "video_rendered",
                "failed",
                native_enum=False,
                create_constraint=True,
                name="session_status",
            ),
            nullable=False,
        ),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("spec_path", sa.Text(), nullable=False),
        sa.Column("spec_sha256", sa.Text(), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("workspace_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("public_id"),
    )


def downgrade() -> None:
    op.drop_table("sessions")
