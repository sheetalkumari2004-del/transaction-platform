"""
Import worker.

Run with: python -m app.workers.import_worker

Reliability design (assignment section 15, "worker failure should not
permanently lose an import"):

1. Jobs are consumed from a Redis Stream consumer group (see
   redis_utils/queue.py). An entry is only ACKed after the corresponding
   batch of rows has been committed to Postgres. If the worker crashes
   mid-batch, the entry stays in the Pending Entries List.
2. Any worker instance periodically calls XAUTOCLAIM to pick up entries
   that have been idle (unacked) longer than WORKER_CLAIM_IDLE_MS -- this
   is what "after the worker is restarted, recover the work" means in
   practice: a second worker (or the same one after restart) reclaims it.
3. Progress is persisted to Postgres (processed/successful/failed_rows,
   last_committed_row) after every batch, not just at the end, so
   GET /imports/{id} reflects real progress and a restart resumes rather
   than starts over.
4. Resuming skips rows up to last_committed_row (by row number) rather
   than re-validating/re-inserting them. We re-read from the start of the
   file to get there rather than seeking a byte offset, because CSV rows
   can contain quoted multi-line fields, which makes byte offsets an
   unsafe resume point; this is a deliberate simplicity/correctness
   trade-off documented in ARCHITECTURE.md -- resuming skips
   already-committed rows cheaply (just reading, not writing), which is
   what actually matters for correctness.
5. Duplicate inserts (same job reprocessing a row it technically already
   committed, or duplicate transaction_id across files/imports) are
   absorbed by the DB UNIQUE constraint via `ON CONFLICT DO NOTHING`,
   which is the actual, final uniqueness guarantee -- not the worker's
   bookkeeping.
"""

import asyncio
import csv
import os
import signal
import socket
from datetime import datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import AsyncSessionLocal
from app.models.import_job import ImportError as ImportErrorModel
from app.models.import_job import ImportJob, ImportStatus
from app.models.transaction import Transaction
from app.redis_utils.cache import invalidate_account_summary_cache
from app.redis_utils.queue import ack_job, ensure_group, reclaim_stale_jobs, read_new_jobs
from app.services.validation import validate_row

configure_logging()
logger = get_logger()
settings = get_settings()

MAX_RETRIES = 3
CONSUMER_NAME = f"worker-{socket.gethostname()}-{os.getpid()}"

_shutdown = False


def _handle_shutdown(*_args):
    global _shutdown
    _shutdown = True


async def process_entry(entry_id: str, import_job_id: str) -> None:
    log = logger.bind(import_job_id=import_job_id, entry_id=entry_id, consumer=CONSUMER_NAME)

    async with AsyncSessionLocal() as db:
        job = await _load_job(db, import_job_id)
        if job is None:
            log.warning("import_job_not_found_acking_and_dropping")
            await ack_job(entry_id)
            return

        if job.status == ImportStatus.COMPLETED:
            log.info("import_already_completed_acking")
            await ack_job(entry_id)
            return

        if job.retry_count >= MAX_RETRIES:
            job.status = ImportStatus.FAILED
            job.error_message = f"Exceeded max retries ({MAX_RETRIES})"
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()
            await ack_job(entry_id)
            log.error("import_failed_max_retries")
            return

        try:
            await _run_import(db, job, log)
            await ack_job(entry_id)
        except Exception as exc:
            log.error("import_processing_error", error=str(exc))
            job.retry_count += 1
            job.status = ImportStatus.QUEUED  # will be reclaimed / redelivered
            await db.commit()
            # Deliberately do NOT ack -- entry stays pending and will be
            # reclaimed by XAUTOCLAIM once idle-timeout passes, giving it
            # another attempt up to MAX_RETRIES.
            raise


async def _load_job(db: AsyncSession, import_job_id: str) -> ImportJob | None:
    result = await db.execute(select(ImportJob).where(ImportJob.id == import_job_id))
    return result.scalar_one_or_none()


async def _run_import(db: AsyncSession, job: ImportJob, log) -> None:
    if job.started_at is None:
        job.started_at = datetime.now(timezone.utc)
    job.status = ImportStatus.PROCESSING
    await db.commit()

    with open(job.storage_path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        from app.services.validation import REQUIRED_COLUMNS
        if not REQUIRED_COLUMNS.issubset(set(reader.fieldnames or [])):
            job.status = ImportStatus.FAILED
            job.error_message = f"CSV missing required columns: {REQUIRED_COLUMNS - set(reader.fieldnames or [])}"
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return

        batch_valid: list[dict] = []
        batch_errors: list[dict] = []
        row_number = 0
        seen_in_file: set[str] = set()

        for row in reader:
            row_number += 1
            if row_number <= job.last_committed_row:
                continue  # already committed in a prior (crashed) attempt

            valid, error = validate_row(row, row_number)
            if valid is None:
                batch_errors.append(
                    {"row_number": row_number, "transaction_id": (row.get("transaction_id") or "").strip() or None, "error": error}
                )
            elif valid["transaction_id"] in seen_in_file:
                batch_errors.append(
                    {"row_number": row_number, "transaction_id": valid["transaction_id"], "error": "duplicate transaction_id within this file"}
                )
            else:
                seen_in_file.add(valid["transaction_id"])
                valid["import_job_id"] = job.id
                batch_valid.append(valid)

            if len(batch_valid) + len(batch_errors) >= settings.worker_batch_size:
                await _commit_batch(db, job, batch_valid, batch_errors, row_number)
                batch_valid, batch_errors = [], []

        if batch_valid or batch_errors:
            await _commit_batch(db, job, batch_valid, batch_errors, row_number)

        job.total_rows = row_number
        job.status = ImportStatus.COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        log.info("import_completed", total_rows=row_number, successful=job.successful_rows, failed=job.failed_rows)


async def _commit_batch(db: AsyncSession, job: ImportJob, valid_rows: list[dict], error_rows: list[dict], up_to_row: int) -> None:
    inserted_count = 0
    affected_accounts: set[str] = set()

    if valid_rows:
        stmt = pg_insert(Transaction).values(valid_rows)
        # ON CONFLICT DO NOTHING on the unique transaction_id constraint is
        # the actual duplicate-prevention mechanism (assignment section 7)
        # -- it silently absorbs duplicates across files, retried imports,
        # and retried worker attempts, rather than the app trying to
        # pre-check every row against the DB (which would race anyway).
        stmt = stmt.on_conflict_do_nothing(index_elements=["transaction_id"])
        result = await db.execute(stmt)
        inserted_count = result.rowcount or 0
        affected_accounts = {r["account_id"] for r in valid_rows}
        # Per-row duplicate attribution (which specific rows were dropped
        # by ON CONFLICT) would need a second round trip; we only need the
        # aggregate count for progress reporting, so we skip it.

    if error_rows:
        await db.execute(insert(ImportErrorModel), [
            {"import_job_id": job.id, "row_number": e["row_number"], "transaction_id": e["transaction_id"], "error": e["error"]}
            for e in error_rows
        ])

    job.processed_rows = up_to_row
    job.successful_rows += inserted_count
    job.failed_rows += len(error_rows) + (len(valid_rows) - inserted_count)
    job.last_committed_row = up_to_row

    await db.execute(
        update(ImportJob)
        .where(ImportJob.id == job.id)
        .values(
            processed_rows=job.processed_rows,
            successful_rows=job.successful_rows,
            failed_rows=job.failed_rows,
            last_committed_row=job.last_committed_row,
            status=job.status,
            started_at=job.started_at,
        )
    )
    await db.commit()

    for account_id in affected_accounts:
        await invalidate_account_summary_cache(account_id)


async def worker_loop() -> None:
    await ensure_group()
    log = logger.bind(consumer=CONSUMER_NAME)
    log.info("worker_started")

    while not _shutdown:
        try:
            reclaimed = await reclaim_stale_jobs(CONSUMER_NAME, idle_ms=settings.worker_claim_idle_ms)
            for entry_id, fields in reclaimed:
                await process_entry(entry_id, fields["import_job_id"])

            entries = await read_new_jobs(CONSUMER_NAME, count=1, block_ms=5000)
            for entry_id, fields in entries:
                await process_entry(entry_id, fields["import_job_id"])
        except Exception as exc:
            log.error("worker_loop_error", error=str(exc))
            await asyncio.sleep(2)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    asyncio.run(worker_loop())
