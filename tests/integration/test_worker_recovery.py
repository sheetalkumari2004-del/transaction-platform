"""
Assignment section 25 "Failure Test": worker failure/restart recovery.

Rather than literally kill -9'ing a subprocess (flaky and slow in CI),
these tests exercise the recovery logic directly:
  1. `_run_import` is given a job whose `last_committed_row` already
     reflects a prior partial attempt, and we assert it resumes rather
     than reprocesses those rows.
  2. `process_entry` is made to fail partway through (simulating a crash),
     and we assert the job is left in a resumable state (QUEUED,
     retry_count incremented, NOT acked) rather than lost, then that a
     second attempt completes it.

Each test generates its own unique transaction_id prefix. The schema is
created once per test session (not reset between tests), and
transaction_id has a real UNIQUE constraint -- reusing literal IDs like
"TXN-R-1" across tests would hit that constraint on the second test and
look like an application bug when it's actually a test-data collision.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.import_job import ImportJob, ImportStatus
from app.models.transaction import Transaction
from app.workers import import_worker

pytestmark = pytest.mark.asyncio


def _csv_content(prefix: str) -> str:
    return (
        "transaction_id,account_id,type,amount,currency,timestamp\n"
        f"{prefix}-1,ACC-R,CREDIT,10.00,USD,2026-09-01T10:00:00Z\n"
        f"{prefix}-2,ACC-R,CREDIT,20.00,USD,2026-09-01T10:01:00Z\n"
        f"{prefix}-3,ACC-R,CREDIT,30.00,USD,2026-09-01T10:02:00Z\n"
    )


async def _make_job(tmp_path, client_id, idempotency_key, prefix: str) -> ImportJob:
    csv_path = tmp_path / f"{prefix}.csv"
    csv_path.write_text(_csv_content(prefix))

    async with AsyncSessionLocal() as db:
        job = ImportJob(
            client_id=client_id,
            original_filename="r.csv",
            storage_path=str(csv_path),
            status=ImportStatus.QUEUED,
            idempotency_key=idempotency_key,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job


async def test_resume_skips_already_committed_rows(tmp_path, test_client_and_key):
    client, _key = test_client_and_key
    prefix = "TXN-RESUME-A"
    job = await _make_job(tmp_path, client.id, "resume-test-1", prefix)

    # Simulate row 1 already having been committed by a prior (crashed) attempt.
    async with AsyncSessionLocal() as db:
        row = Transaction(
            transaction_id=f"{prefix}-1", account_id="ACC-R", type="CREDIT",
            amount="10.00", currency="USD", timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
            import_job_id=job.id,
        )
        db.add(row)
        db_job = (await db.execute(select(ImportJob).where(ImportJob.id == job.id))).scalar_one()
        db_job.last_committed_row = 1
        db_job.processed_rows = 1
        db_job.successful_rows = 1
        await db.commit()

    async with AsyncSessionLocal() as db:
        db_job = (await db.execute(select(ImportJob).where(ImportJob.id == job.id))).scalar_one()
        await import_worker._run_import(db, db_job, import_worker.logger)

    async with AsyncSessionLocal() as db:
        final_job = (await db.execute(select(ImportJob).where(ImportJob.id == job.id))).scalar_one()
        assert final_job.status == ImportStatus.COMPLETED
        # Row 1 was skipped (not reprocessed), rows 2 and 3 were newly committed.
        assert final_job.successful_rows == 3
        assert final_job.total_rows == 3

        rows = (
            await db.execute(select(Transaction).where(Transaction.transaction_id == f"{prefix}-1"))
        ).scalars().all()
        assert len(rows) == 1  # not duplicated


async def test_failed_attempt_is_left_resumable_not_lost(tmp_path, test_client_and_key, monkeypatch):
    client, _key = test_client_and_key
    prefix = "TXN-RESUME-B"
    job = await _make_job(tmp_path, client.id, "resume-test-2", prefix)

    call_count = {"n": 0}
    real_commit_batch = import_worker._commit_batch

    async def flaky_commit_batch(db, job_arg, valid_rows, error_rows, up_to_row):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated worker crash mid-batch")
        return await real_commit_batch(db, job_arg, valid_rows, error_rows, up_to_row)

    monkeypatch.setattr(import_worker, "_commit_batch", flaky_commit_batch)
    monkeypatch.setattr(get_settings(), "worker_batch_size", 1, raising=False)

    fake_entry_id = "0-1"
    with pytest.raises(RuntimeError):
        await import_worker.process_entry(fake_entry_id, str(job.id))

    async with AsyncSessionLocal() as db:
        after_failure = (await db.execute(select(ImportJob).where(ImportJob.id == job.id))).scalar_one()
        assert after_failure.status == ImportStatus.QUEUED
        assert after_failure.retry_count == 1

    monkeypatch.setattr(import_worker, "_commit_batch", real_commit_batch)
    await import_worker.process_entry(fake_entry_id, str(job.id))

    async with AsyncSessionLocal() as db:
        final_job = (await db.execute(select(ImportJob).where(ImportJob.id == job.id))).scalar_one()
        assert final_job.status == ImportStatus.COMPLETED
        assert final_job.successful_rows == 3
