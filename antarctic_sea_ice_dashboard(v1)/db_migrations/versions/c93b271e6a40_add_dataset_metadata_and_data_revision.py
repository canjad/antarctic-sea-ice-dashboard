"""Add optional scientific declarations and persistent dataset revisions.

Revision ID: c93b271e6a40
Revises: 74d17ca12f33
Create Date: 2026-10-19
"""

from collections.abc import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "c93b271e6a40"
down_revision: str | Sequence[str] | None = "74d17ca12f33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve existing rows and initialise every dataset revision to one."""
    # Native column addition keeps the parent table and row foreign keys intact.
    # Each legacy dataset receives a distinct identity; a shared server default
    # would allow stale expectations to match a later reuse of an integer ID.
    op.add_column(
        "uploaded_dataset", sa.Column("identity_key", sa.Text(), nullable=True)
    )
    connection = op.get_bind()
    dataset_ids = connection.execute(sa.text("SELECT id FROM uploaded_dataset"))
    for dataset_id in dataset_ids.scalars():
        connection.execute(
            sa.text(
                "UPDATE uploaded_dataset SET identity_key = :identity_key "
                "WHERE id = :dataset_id"
            ),
            {"identity_key": uuid4().hex, "dataset_id": dataset_id},
        )
    op.create_index(
        "uq_uploaded_dataset_identity_key",
        "uploaded_dataset",
        ["identity_key"],
        unique=True,
    )

    for column_name in ("variable_key", "observation_month", "unit", "grid_key"):
        op.add_column(
            "uploaded_dataset", sa.Column(column_name, sa.Text(), nullable=True)
        )

    # The literal default supplies revision 1 for all existing rows in the same
    # statement that adds NOT NULL and the positive-value constraint. An inline
    # column CHECK works on PostgreSQL and SQLite without rebuilding the parent
    # table, preserving row foreign keys and avoiding cascading deletion risks.
    op.execute(
        "ALTER TABLE uploaded_dataset ADD COLUMN data_revision INTEGER "
        "NOT NULL DEFAULT 1 CONSTRAINT ck_uploaded_dataset_data_revision "
        "CHECK (data_revision > 0)"
    )


def downgrade() -> None:
    """Remove only this revision's columns; SQLite requires version 3.35+."""
    op.drop_index("uq_uploaded_dataset_identity_key", table_name="uploaded_dataset")
    # The CHECK belongs to the dropped column, so native DROP COLUMN removes it.
    # No batch table replacement or foreign-key configuration change is needed.
    for column_name in (
        "data_revision",
        "grid_key",
        "unit",
        "observation_month",
        "variable_key",
        "identity_key",
    ):
        op.drop_column("uploaded_dataset", column_name)
