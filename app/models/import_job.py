import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class ImportStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True)

    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, name="import_status"), default=ImportStatus.QUEUED, nullable=False, index=True
    )

    total_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    processed_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    successful_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    failed_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Idempotency: a client-supplied key (or content hash of the file) that
    # lets us detect "the same import request retried" and return the
    # original import instead of double-queuing it.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)

    # Bookkeeping for stream-based recovery: last byte offset / row number
    # successfully committed, so a restarted worker resumes rather than
    # reprocesses the whole file.
    last_committed_row: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    errors: Mapped[list["ImportError"]] = relationship(back_populates="import_job", cascade="all, delete-orphan")


class ImportError(Base):
    __tablename__ = "import_errors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    import_job: Mapped["ImportJob"] = relationship(back_populates="errors")

    __table_args__ = ()
