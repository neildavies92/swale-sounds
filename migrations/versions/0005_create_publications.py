"""Preserve manual publication facts as append-only history."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "video_render_id",
            sa.Integer(),
            sa.ForeignKey("render_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("plan_sha256", sa.Text(), nullable=False),
        sa.Column("plan_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "platform", "external_id", name="uq_publication_external"
        ),
        sa.CheckConstraint(
            "platform = 'youtube'", name="publication_platform"
        ),
        sa.CheckConstraint("status = 'published'", name="publication_status"),
        sa.CheckConstraint(
            "length(trim(external_id)) > 0",
            name="publication_external_nonempty",
        ),
        sa.CheckConstraint(
            "plan_version > 0", name="publication_plan_version"
        ),
        sa.CheckConstraint(
            "length(plan_sha256) = 64 AND plan_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="publication_digest",
        ),
    )
    op.create_index(
        "ix_publications_session_id", "publications", ["session_id"]
    )
    op.create_index(
        "ix_publications_video_render_id", "publications", ["video_render_id"]
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER publications_no_{operation.lower()} "
            f"BEFORE {operation} ON publications BEGIN "
            "SELECT RAISE(ABORT, 'Publication history is immutable'); END"
        )
    op.execute(
        "CREATE TRIGGER "
        "publications_video_identity BEFORE INSERT ON publications "
        "WHEN NOT EXISTS (SELECT 1 FROM "
        "render_runs WHERE id = NEW.video_render_id "
        "AND session_id = NEW.session_id AND "
        "stage = 'video' AND status = 'succeeded') "
        "BEGIN SELECT RAISE(ABORT, 'Publication "
        "requires a successful Session video'); END"
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM publications")):
        raise RuntimeError(
            "Cannot downgrade while "
            "Publications exist; preserve their history."
        )
    op.drop_table("publications")
