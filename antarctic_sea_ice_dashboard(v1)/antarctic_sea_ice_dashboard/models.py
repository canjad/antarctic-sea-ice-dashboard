import reflex as rx
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UploadedDataset(Base):
    """Publicly shared CSV metadata; column_names_json stores an ordered JSON array."""

    __tablename__ = "uploaded_dataset"
    __table_args__ = (
        Index("uq_uploaded_dataset_identity_key", "identity_key", unique=True),
        CheckConstraint("row_count >= 0", name="ck_uploaded_dataset_row_count"),
        CheckConstraint(
            "data_revision > 0", name="ck_uploaded_dataset_data_revision"
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    # A fresh identity prevents stale edits reaching a reused SQLite integer ID.
    # Storage stays nullable for native ALTER TABLE; write paths require a key.
    identity_key: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=lambda: uuid4().hex
    )
    name: Mapped[str] = mapped_column(
        Text, default="", server_default=text("''")
    )
    original_filename: Mapped[str] = mapped_column(
        Text, default="", server_default=text("''")
    )
    column_names_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default=text("'[]'")
    )
    row_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    data_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    variable_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation_month: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    grid_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    source: Mapped[str] = mapped_column(
        Text, default="uploaded_csv", server_default=text("'uploaded_csv'")
    )
    status: Mapped[str] = mapped_column(
        Text, default="pending", server_default=text("'pending'")
    )


class UploadedDatasetRow(Base):
    """Editable cells stored as a JSON array matching the dataset's column order."""

    __tablename__ = "uploaded_dataset_row"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "row_position",
            name="uq_uploaded_dataset_row_position",
        ),
        CheckConstraint(
            "row_position >= 0", name="ck_uploaded_dataset_row_position"
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("uploaded_dataset.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
        default=None,
    )
    row_position: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    cell_values_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default=text("'[]'")
    )


class QualityCheckRun(Base):
    """One immutable snapshot assessment; current quality is derived on read."""

    __tablename__ = "quality_check_run"
    __table_args__ = (
        UniqueConstraint("dataset_identity_key", "attempt_number", name="uq_quality_run_attempt"),
        Index("uq_quality_run_active_dataset", "active_dataset_key", unique=True),
        Index("ix_quality_run_dataset_attempt", "dataset_id", "attempt_number"),
        CheckConstraint("checked_data_revision > 0 AND attempt_number > 0", name="ck_quality_run_revision_attempt"),
        CheckConstraint("run_status IN ('running', 'completed', 'error')", name="ck_quality_run_status"),
        CheckConstraint(
            "scanned_row_count >= 0 AND snapshot_row_count >= 0 AND issue_count >= 0 "
            "AND warning_count >= 0 AND warning_count <= issue_count",
            name="ck_quality_run_counts",
        ),
        CheckConstraint(
            "(run_status = 'completed' AND outcome IN ('passed', 'failed', 'blocked') "
            "AND outcome IS NOT NULL AND report_complete = 1 AND completed_at IS NOT NULL) OR "
            "(run_status IN ('running', 'error') AND outcome IS NULL AND report_complete = 0)",
            name="ck_quality_run_outcome",
        ),
        CheckConstraint(
            "(run_status = 'running' AND active_dataset_key = dataset_identity_key "
            "AND active_dataset_key IS NOT NULL AND completed_at IS NULL) OR "
            "(run_status <> 'running' AND active_dataset_key IS NULL AND completed_at IS NOT NULL)",
            name="ck_quality_run_active",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("uploaded_dataset.id", ondelete="CASCADE"))
    dataset_identity_key: Mapped[str] = mapped_column(Text)
    checked_data_revision: Mapped[int] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer)
    ruleset_version: Mapped[str] = mapped_column(Text)
    profile_key: Mapped[str] = mapped_column(Text)
    profile_version: Mapped[str] = mapped_column(Text)
    reference_identity_json: Mapped[str] = mapped_column(Text)
    active_dataset_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_status: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    scanned_row_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    snapshot_row_count: Mapped[int] = mapped_column(Integer)
    issue_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    warning_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    report_complete: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    summary_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    rule_results_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class QualityIssue(Base):
    """Snapshot row identities survive working-row edits and deletions."""

    __tablename__ = "quality_issue"
    __table_args__ = (
        UniqueConstraint("check_run_id", "issue_number", name="uq_quality_issue_number"),
        Index("ix_quality_issue_run_number", "check_run_id", "issue_number"),
        CheckConstraint("severity IN ('error', 'warning', 'info')", name="ck_quality_issue_severity"),
        CheckConstraint("issue_number > 0", name="ck_quality_issue_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    check_run_id: Mapped[str] = mapped_column(ForeignKey("quality_check_run.id", ondelete="CASCADE"))
    issue_number: Mapped[int] = mapped_column(Integer)
    issue_code: Mapped[str] = mapped_column(Text)
    rule_code: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    snapshot_row_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_row_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    field: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_value_truncated: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)


class ResearchProduct(Base):
    """Publication journal and immutable evidence, independent of working data."""

    __tablename__ = "research_product"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_research_product_idempotency_key"),
        CheckConstraint(
            "publication_status IN ('preparing', 'publishing', 'published', 'blocked', 'failed')",
            name="ck_research_product_status",
        ),
        CheckConstraint(
            "publication_status NOT IN ('publishing', 'published', 'blocked') OR "
            "(manifest_digest IS NOT NULL AND content_digest IS NOT NULL)",
            name="ck_research_product_manifest",
        ),
        Index("ix_research_product_status_created", "publication_status", "created_at"),
        Index("ix_research_product_source_revision", "source_identity", "source_data_revision"),
    )

    product_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(Text)
    # Deliberately no foreign keys: deletion must preserve source evidence.
    source_identity: Mapped[str] = mapped_column(Text)
    source_data_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_status: Mapped[str] = mapped_column(Text)
    attempt_id: Mapped[str] = mapped_column(Text)
    request_json: Mapped[str] = mapped_column(Text)
    manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
