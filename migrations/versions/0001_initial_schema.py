"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-06

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Each ENUM type is referenced by exactly one column below, so letting
    # that column's own CREATE TABLE create it (SQLAlchemy's default
    # behavior) is sufficient -- no separate manual CREATE TYPE needed.
    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("api_key_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("api_key_hash", name="uq_clients_api_key_hash"),
    )

    op.create_table(
        "import_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("status", sa.Enum("QUEUED", "PROCESSING", "COMPLETED", "FAILED", name="import_status"), nullable=False, server_default="QUEUED"),
        sa.Column("total_rows", sa.BigInteger(), nullable=True),
        sa.Column("processed_rows", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("successful_rows", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("last_committed_row", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(2048), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_import_jobs_idempotency_key"),
    )
    op.create_index("ix_import_jobs_client_id", "import_jobs", ["client_id"])
    op.create_index("ix_import_jobs_status", "import_jobs", ["status"])

    op.create_table(
        "import_errors",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("import_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("row_number", sa.BigInteger(), nullable=False),
        sa.Column("transaction_id", sa.String(128), nullable=True),
        sa.Column("error", sa.String(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_import_errors_import_job_id", "import_errors", ["import_job_id"])

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("transaction_id", sa.String(128), nullable=False),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("type", sa.Enum("CREDIT", "DEBIT", name="transaction_type"), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("import_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("transaction_id", name="uq_transactions_transaction_id"),
    )
    op.create_index("ix_transactions_transaction_id", "transactions", ["transaction_id"])
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_timestamp", "transactions", ["timestamp"])
    op.create_index("ix_transactions_import_job_id", "transactions", ["import_job_id"])
    # Composite indexes serving the most common query shapes directly
    # (assignment section 18: "avoid missing indexes for common filters").
    op.create_index("ix_transactions_account_type_ts", "transactions", ["account_id", "type", "timestamp"])
    op.create_index("ix_transactions_account_ts", "transactions", ["account_id", "timestamp"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("import_errors")
    op.drop_table("import_jobs")
    op.drop_table("clients")
    postgresql.ENUM(name="transaction_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="import_status").drop(op.get_bind(), checkfirst=True)
