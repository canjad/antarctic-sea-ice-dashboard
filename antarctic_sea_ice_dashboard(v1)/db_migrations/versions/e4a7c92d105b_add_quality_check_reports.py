"""Add revision-bound quality reports without altering existing uploaded data.

Revision ID: e4a7c92d105b
Revises: c93b271e6a40
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e4a7c92d105b"
down_revision: str | Sequence[str] | None = "c93b271e6a40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create empty child tables; legacy datasets remain not checked."""
    op.create_table(
        "quality_check_run",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("dataset_identity_key", sa.Text(), nullable=False),
        sa.Column("checked_data_revision", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("ruleset_version", sa.Text(), nullable=False),
        sa.Column("profile_key", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("reference_identity_json", sa.Text(), nullable=False),
        sa.Column("active_dataset_key", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_status", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("scanned_row_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("snapshot_row_count", sa.Integer(), nullable=False),
        sa.Column("issue_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("warning_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("report_complete", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("summary_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("rule_results_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["dataset_id"], ["uploaded_dataset.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dataset_identity_key", "attempt_number", name="uq_quality_run_attempt"),
        sa.CheckConstraint("checked_data_revision > 0 AND attempt_number > 0", name="ck_quality_run_revision_attempt"),
        sa.CheckConstraint("run_status IN ('running', 'completed', 'error')", name="ck_quality_run_status"),
        sa.CheckConstraint(
            "(run_status = 'completed' AND outcome IN ('passed', 'failed', 'blocked') "
            "AND outcome IS NOT NULL AND report_complete = 1 AND completed_at IS NOT NULL) OR "
            "(run_status IN ('running', 'error') AND outcome IS NULL AND report_complete = 0)",
            name="ck_quality_run_outcome",
        ),
        sa.CheckConstraint(
            "(run_status = 'running' AND active_dataset_key = dataset_identity_key "
            "AND active_dataset_key IS NOT NULL AND completed_at IS NULL) OR "
            "(run_status <> 'running' AND active_dataset_key IS NULL AND completed_at IS NOT NULL)",
            name="ck_quality_run_active",
        ),
        sa.CheckConstraint(
            "scanned_row_count >= 0 AND snapshot_row_count >= 0 AND issue_count >= 0 "
            "AND warning_count >= 0 AND warning_count <= issue_count",
            name="ck_quality_run_counts",
        ),
    )
    # Multiple NULL values allow historical reports; one non-NULL incarnation
    # key protects active starts across backend processes on both target dialects.
    op.create_index("uq_quality_run_active_dataset", "quality_check_run", ["active_dataset_key"], unique=True)
    op.create_index("ix_quality_run_dataset_attempt", "quality_check_run", ["dataset_id", "attempt_number"])
    op.create_table(
        "quality_issue",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("check_run_id", sa.Text(), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("issue_code", sa.Text(), nullable=False),
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        # These are snapshot facts, deliberately not working-row foreign keys.
        sa.Column("snapshot_row_id", sa.Integer(), nullable=True),
        sa.Column("snapshot_row_position", sa.Integer(), nullable=True),
        sa.Column("field", sa.Text(), nullable=True),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("raw_value_truncated", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["check_run_id"], ["quality_check_run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("check_run_id", "issue_number", name="uq_quality_issue_number"),
        sa.CheckConstraint("severity IN ('error', 'warning', 'info')", name="ck_quality_issue_severity"),
        sa.CheckConstraint("issue_number > 0", name="ck_quality_issue_number"),
    )
    op.create_index("ix_quality_issue_run_number", "quality_issue", ["check_run_id", "issue_number"])


def downgrade() -> None:
    """Remove quality reports only; uploaded records and revisions are retained."""
    op.drop_index("ix_quality_issue_run_number", table_name="quality_issue")
    op.drop_table("quality_issue")
    op.drop_index("ix_quality_run_dataset_attempt", table_name="quality_check_run")
    op.drop_index("uq_quality_run_active_dataset", table_name="quality_check_run")
    op.drop_table("quality_check_run")
