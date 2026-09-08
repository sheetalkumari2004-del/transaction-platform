import asyncio
import io

import pytest

pytestmark = pytest.mark.asyncio

VALID_CSV = (
    "transaction_id,account_id,type,amount,currency,timestamp\n"
    "TXN-100,ACC-2001,CREDIT,100.00,USD,2026-09-01T10:00:00Z\n"
    "TXN-101,ACC-2001,DEBIT,25.00,USD,2026-09-01T10:01:00Z\n"
    "TXN-102,ACC-2001,DEBIT,-5.00,USD,2026-09-01T10:02:00Z\n"  # invalid: negative amount
)


async def _wait_for_status(api_client, headers, import_id, target_statuses, timeout=15):
    for _ in range(timeout * 2):
        resp = await api_client.get(f"/api/v1/imports/{import_id}", headers=headers)
        body = resp.json()
        if body["status"] in target_statuses:
            return body
        await asyncio.sleep(0.5)
    raise TimeoutError(f"Import did not reach {target_statuses} in time, last={body}")


async def test_missing_api_key_rejected(api_client):
    resp = await api_client.get("/api/v1/transactions")
    assert resp.status_code == 401


async def test_create_import_returns_queued(api_client, test_client_and_key):
    _client, raw_key = test_client_and_key
    files = {"file": ("transactions.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")}
    resp = await api_client.post("/api/v1/imports", headers={"X-API-Key": raw_key}, files=files)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "QUEUED"
    assert "import_id" in body


async def test_non_csv_file_rejected(api_client, test_client_and_key):
    _client, raw_key = test_client_and_key
    files = {"file": ("transactions.txt", io.BytesIO(b"not a csv"), "text/plain")}
    resp = await api_client.post("/api/v1/imports", headers={"X-API-Key": raw_key}, files=files)
    assert resp.status_code == 400


async def test_retried_import_with_idempotency_key_returns_same_job(api_client, test_client_and_key):
    _client, raw_key = test_client_and_key
    headers = {"X-API-Key": raw_key, "Idempotency-Key": "retry-test-key-1"}
    files = {"file": ("transactions.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")}

    first = await api_client.post("/api/v1/imports", headers=headers, files=files)
    files2 = {"file": ("transactions.csv", io.BytesIO(VALID_CSV.encode()), "text/csv")}
    second = await api_client.post("/api/v1/imports", headers=headers, files=files2)

    assert first.json()["import_id"] == second.json()["import_id"]


async def test_import_not_found_returns_404(api_client, test_client_and_key):
    _client, raw_key = test_client_and_key
    resp = await api_client.get("/api/v1/imports/00000000-0000-0000-0000-000000000000", headers={"X-API-Key": raw_key})
    assert resp.status_code == 404
