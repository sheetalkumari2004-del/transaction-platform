"""
Assignment section 25: "Test concurrent submission of duplicate
transactions." This exercises the actual guarantee (DB unique constraint
+ ON CONFLICT DO NOTHING) directly at the insert layer, which is more
deterministic than racing two full HTTP file uploads and is what the
worker's batch-insert path uses under the hood.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.transaction import Transaction

pytestmark = pytest.mark.asyncio


async def _insert_attempt(session_factory, row: dict):
    async with session_factory() as db:
        stmt = pg_insert(Transaction).values(**row).on_conflict_do_nothing(index_elements=["transaction_id"])
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount or 0


async def test_concurrent_duplicate_inserts_result_in_one_row(db_session):
    from app.db.session import AsyncSessionLocal

    row = dict(
        transaction_id="TXN-CONCURRENT-1",
        account_id="ACC-CONC",
        type="CREDIT",
        amount=Decimal("50.00"),
        currency="USD",
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    results = await asyncio.gather(*[_insert_attempt(AsyncSessionLocal, row) for _ in range(10)])

    assert sum(results) == 1  # exactly one of the 10 concurrent attempts actually inserted

    count = (
        await db_session.execute(
            select(func.count()).select_from(Transaction).where(Transaction.transaction_id == "TXN-CONCURRENT-1")
        )
    ).scalar_one()
    assert count == 1
