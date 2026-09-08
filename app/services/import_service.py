"""
Handles POST /api/v1/imports.

Duplicate-request handling (assignment section 7, "retried import
request"): the client may supply an Idempotency-Key header. If it does
and a job with that key already exists for this client, we return the
EXISTING job instead of creating a new one -- this is what makes a
retried POST safe. If no header is supplied, we fall back to a SHA-256 of
the file content (computed incrementally while streaming to disk, so we
never buffer the whole file in memory) scoped to the client, which still
catches "the exact same file re-uploaded" even without client
cooperation, though a client-supplied key is the more robust guarantee
against network-retry duplicates (a byte-for-byte identical retry hashes
the same either way).
"""

import hashlib
import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.client import Client
from app.models.import_job import ImportJob, ImportStatus
from app.redis_utils.queue import enqueue_import_job, ensure_group

settings = get_settings()
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/data/uploads"))
CHUNK_SIZE = 1024 * 1024  # 1 MB


async def create_import(
    db: AsyncSession,
    client: Client,
    file: UploadFile,
    idempotency_key_header: str | None,
) -> tuple[ImportJob, bool]:
    """Returns (import_job, was_existing)."""

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be a .csv file")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4()
    storage_path = UPLOAD_DIR / f"{job_id}.csv"

    hasher = hashlib.sha256()
    bytes_written = 0

    with open(storage_path, "wb") as out:
        while chunk := await file.read(CHUNK_SIZE):
            bytes_written += len(chunk)
            if bytes_written > settings.upload_max_bytes:
                out.close()
                storage_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="File exceeds maximum allowed size",
                )
            hasher.update(chunk)
            out.write(chunk)

    if bytes_written == 0:
        storage_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    idempotency_key = idempotency_key_header or f"content:{hasher.hexdigest()}:{client.id}"

    existing = await db.execute(select(ImportJob).where(ImportJob.idempotency_key == idempotency_key))
    existing_job = existing.scalar_one_or_none()
    if existing_job is not None:
        # This request is a retry of one we've already accepted -- discard
        # the newly written duplicate file and hand back the original job.
        storage_path.unlink(missing_ok=True)
        return existing_job, True

    import_job = ImportJob(
        id=job_id,
        client_id=client.id,
        original_filename=file.filename,
        storage_path=str(storage_path),
        status=ImportStatus.QUEUED,
        idempotency_key=idempotency_key,
    )
    db.add(import_job)
    try:
        await db.commit()
    except IntegrityError:
        # Race: two concurrent requests with the same idempotency key both
        # passed the SELECT above. The unique constraint is the real
        # guarantee; lose the race gracefully and return the winner.
        await db.rollback()
        storage_path.unlink(missing_ok=True)
        result = await db.execute(select(ImportJob).where(ImportJob.idempotency_key == idempotency_key))
        return result.scalar_one(), True

    await db.refresh(import_job)

    await ensure_group()
    await enqueue_import_job(str(import_job.id))

    return import_job, False
