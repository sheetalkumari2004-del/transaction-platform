"""
Generates a synthetic transaction CSV for large-file import testing
(assignment section 16: up to 500,000 rows) and for seeding data before
running the read-path load test.

Usage:
    python load_test/generate_large_csv.py --rows 500000 --out load_test/large_transactions.csv
"""

import argparse
import csv
import random
from datetime import datetime, timedelta, timezone

ACCOUNTS = [f"ACC-{i:04d}" for i in range(1, 201)]
CURRENCIES = ["USD", "EUR", "GBP", "INR", "JPY"]
TYPES = ["CREDIT", "DEBIT"]


def generate(rows: int, out_path: str, invalid_rate: float) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["transaction_id", "account_id", "type", "amount", "currency", "timestamp"])
        for i in range(1, rows + 1):
            ts = start + timedelta(seconds=i * 3)
            if random.random() < invalid_rate:
                amount = "-1.00"  # deliberately invalid, to exercise the error path at scale
            else:
                amount = f"{random.uniform(1, 10000):.2f}"
            writer.writerow(
                [
                    f"TXN-{i:07d}",
                    random.choice(ACCOUNTS),
                    random.choice(TYPES),
                    amount,
                    random.choice(CURRENCIES),
                    ts.isoformat().replace("+00:00", "Z"),
                ]
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=500_000)
    parser.add_argument("--out", type=str, default="load_test/large_transactions.csv")
    parser.add_argument("--invalid-rate", type=float, default=0.02)
    args = parser.parse_args()
    generate(args.rows, args.out, args.invalid_rate)
    print(f"Wrote {args.rows} rows to {args.out}")
