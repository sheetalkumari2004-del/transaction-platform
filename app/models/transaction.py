import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TransactionType(str, enum.Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # transaction_id is the client-supplied natural key. It is the FINAL
    # uniqueness guarantee (enforced by a DB unique constraint), which is
    # what actually stops duplicates -- app-level checks are only an
    # optimization to avoid unnecessary round trips.
    transaction_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)

    account_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    type: Mapped[TransactionType] = mapped_column(Enum(TransactionType, name="transaction_type"), nullable=False)

    # NUMERIC(18,2) avoids float rounding issues for money; scale can be
    # widened per-currency later if sub-cent precision is ever needed.
    amount: Mapped[str] = mapped_column(Numeric(18, 2), nullable=False)

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_transactions_transaction_id"),
        # Composite index to serve the listing endpoint's most common filter
        # combination (account + type + date range) without a seq scan.
        Index("ix_transactions_account_type_ts", "account_id", "type", "timestamp"),
        Index("ix_transactions_account_ts", "account_id", "timestamp"),
    )
