"""Add provenance-tracked source assets belonging to sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "music_source",
                "artwork_source",
                "ambience_source",
                native_enum=False,
                create_constraint=True,
                name="asset_kind",
            ),
            nullable=False,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.Text()),
        sa.Column("format_name", sa.Text()),
        sa.Column("codec_name", sa.Text()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("sample_rate", sa.Integer()),
        sa.Column("channels", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_model", sa.Text()),
        sa.Column("provider_plan", sa.Text()),
        sa.Column("generation_prompt", sa.Text()),
        sa.Column("generation_parameters", sa.JSON(none_as_null=True)),
        sa.Column("licence_notes", sa.Text()),
        sa.Column("licence_url", sa.Text()),
        sa.Column("licence_version", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "session_id", "kind", "sha256", name="uq_asset_content"
        ),
        sa.UniqueConstraint("path", name="uq_asset_path"),
        sa.CheckConstraint("size_bytes >= 0", name="asset_size_nonnegative"),
    )
    op.create_index("ix_assets_session_id", "assets", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_assets_session_id", table_name="assets")
    op.drop_table("assets")
