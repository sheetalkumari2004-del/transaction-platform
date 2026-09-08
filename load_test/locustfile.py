"""
Performance/load test (assignment section 26).

Usage:
    pip install locust
    locust -f load_test/locustfile.py --host http://localhost:8000 \
        --users 100 --spawn-rate 10 --run-time 60s --headless \
        --csv=load_test/results/run1

Set API_KEY to a real key created via `python -m scripts.create_client`.
Set SEED_ACCOUNT_IDS / SEED_TRANSACTION_IDS (comma-separated) to real
values already in the DB so GET requests hit realistic data rather than
404s -- see load_test/seed_data.py to generate and load a large dataset
first.
"""

import os
import random

from locust import HttpUser, between, task

API_KEY = os.environ.get("LOAD_TEST_API_KEY", "changeme")
ACCOUNT_IDS = (os.environ.get("SEED_ACCOUNT_IDS") or "ACC-1001,ACC-1002,ACC-1003").split(",")
TRANSACTION_IDS = (os.environ.get("SEED_TRANSACTION_IDS") or "TXN-001,TXN-002,TXN-003").split(",")


class TransactionPlatformUser(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self):
        self.client.headers.update({"X-API-Key": API_KEY})

    @task(5)
    def list_transactions(self):
        account_id = random.choice(ACCOUNT_IDS)
        self.client.get(
            f"/api/v1/transactions?account_id={account_id}&page=1&limit=50",
            name="/api/v1/transactions [list]",
        )

    @task(3)
    def get_transaction(self):
        txn_id = random.choice(TRANSACTION_IDS)
        self.client.get(f"/api/v1/transactions/{txn_id}", name="/api/v1/transactions/[id]")

    @task(4)
    def account_summary(self):
        account_id = random.choice(ACCOUNT_IDS)
        self.client.get(f"/api/v1/accounts/{account_id}/summary", name="/api/v1/accounts/[id]/summary")

    @task(1)
    def health_ready(self):
        self.client.get("/health/ready")
