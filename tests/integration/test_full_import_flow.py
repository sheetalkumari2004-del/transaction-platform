"""
Full end-to-end flow: upload -> worker processes -> query results.

Unlike the other integration tests, this one needs an actual worker
process consuming the Redis stream, not just Postgres/Redis instances.
Run with the worker also up, e.g.:

    docker compose -f docker-compose.test.yml up -d
    TEST_DATABASE_URL=... TEST_REDIS_URL=... UPLOAD_DIR=/tmp/uploads \
        python -m app.workers.import_worker &
    RUN_WORKER_TESTS=1 pytest tests/integration/test_full_import_flow.py

Skipped by default so the fast unit+API suite doesn't hang waiting for a
worker that isn't running.
"""

import asyncio
import io
import os

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not os.environ.get("RUN_WORKER_TESTS"), reason="requires a running worker process; set RUN_WORKER_TESTS=1")]

CSV_CONTENT = (
    "transaction_id,account_id,type,amount,currency,timestamp\n"
    "TXN-E2E-1,ACC-E2E,CREDIT,1000.00,USD,2026-09-01T10:00:00Z\n"
    "TXN-E2E-2,ACC-E2E,DEBIT,300.00,USD,2026-09-01T10:05:00Z\n"
    "TXN-E2E-2,ACC-E2E,DEBIT,300.00,USD,2026-09-01T10:05:00Z\n"  # duplicate transaction_id
    "TXN-E2E-3,ACC-E2E,CREDIT,abc,USD,2026-09-01T10:06:00Z\n"  # invalid amount
)


async def _wait_for_completion(api_client, headers, import_id, timeout=30):
    for _ in range(timeout * 2):
        resp = await api_client.get(f"/api/v1/imports/{import_id}", headers=headers)
        body = resp.json()
        if body["status"] in ("COMPLETED", "FAILED"):
            return body
        await asyncio.sleep(0.5)
    raise TimeoutError(f"Import stuck, last={body}")


async def test_end_to_end_import_and_query(api_client, test_client_and_key):
    _client, raw_key = test_client_and_key
    headers = {"X-API-Key": raw_key}
    files = {"file": ("e2e.csv", io.BytesIO(CSV_CONTENT.encode()), "text/csv")}

    create_resp = await api_client.post("/api/v1/imports", headers=headers, files=files)
    import_id = create_resp.json()["import_id"]

    status_body = await _wait_for_completion(api_client, headers, import_id)
    assert status_body["status"] == "COMPLETED"
    assert status_body["successful_rows"] == 2  # TXN-E2E-1 and the first TXN-E2E-2
    assert status_body["failed_rows"] == 2  # duplicate TXN-E2E-2 + invalid amount row

    errors_resp = await api_client.get(f"/api/v1/imports/{import_id}/errors", headers=headers)
    assert errors_resp.json()["total"] == 2

    summary_resp = await api_client.get("/api/v1/accounts/ACC-E2E/summary", headers=headers)
    summary = summary_resp.json()
    assert summary["total_credits"] == "1000.00"
    assert summary["total_debits"] == "300.00"
    assert summary["balance"] == "700.00"

    txn_resp = await api_client.get("/api/v1/transactions/TXN-E2E-1", headers=headers)
    assert txn_resp.status_code == 200
    assert txn_resp.json()["account_id"] == "ACC-E2E"
