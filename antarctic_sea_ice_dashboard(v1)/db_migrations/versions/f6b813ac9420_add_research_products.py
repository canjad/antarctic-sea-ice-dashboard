"""Add an independent publication journal; no legacy product is backfilled.

Revision ID: f6b813ac9420
Revises: e4a7c92d105b
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f6b813ac9420"
down_revision: str | Sequence[str] | None = "e4a7c92d105b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create an empty journal on SQLite/PostgreSQL without touching CSV rows."""
    op.create_table(
        "research_product",
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_identity", sa.Text(), nullable=False),
        sa.Column("source_data_revision", sa.Integer(), nullable=True),
        sa.Column("publication_status", sa.Text(), nullable=False),
        sa.Column("attempt_id", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=True),
        sa.Column("manifest_digest", sa.Text(), nullable=True),
        sa.Column("content_digest", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("product_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_research_product_idempotency_key"),
        sa.CheckConstraint(
            "publication_status IN ('preparing', 'publishing', 'published', 'blocked', 'failed')",
            name="ck_research_product_status",
        ),
        sa.CheckConstraint(
            "publication_status NOT IN ('publishing', 'published', 'blocked') OR "
            "(manifest_digest IS NOT NULL AND content_digest IS NOT NULL)",
            name="ck_research_product_manifest",
        ),
    )
    op.create_index("ix_research_product_status_created", "research_product", ["publication_status", "created_at"])
    op.create_index("ix_research_product_source_revision", "research_product", ["source_identity", "source_data_revision"])


def downgrade() -> None:
    """Remove journal metadata only; retained files require operator handling."""
    op.drop_index("ix_research_product_status_created", table_name="research_product")
    op.drop_index("ix_research_product_source_revision", table_name="research_product")
    op.drop_table("research_product")
