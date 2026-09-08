from datetime import datetime

from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


async def get_transaction(db: AsyncSession, transaction_id: str) -> Transaction | None:
    result = await db.execute(select(Transaction).where(Transaction.transaction_id == transaction_id))
    return result.scalar_one_or_none()


async def list_transactions(
    db: AsyncSession,
    account_id: str | None,
    txn_type: str | None,
    currency: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    page: int,
    limit: int,
    sort_by: str,
    sort_dir: str,
) -> tuple[list[Transaction], int]:
    """
    All filtering happens in the WHERE clause and pagination via
    LIMIT/OFFSET at the database -- never fetch-then-filter in Python.
    account_id/type/currency/timestamp are all indexed (see
    models/transaction.py) so this stays index-backed as the table grows.
    """
    stmt = select(Transaction)
    count_stmt = select(func.count()).select_from(Transaction)

    if account_id:
        stmt = stmt.where(Transaction.account_id == account_id)
        count_stmt = count_stmt.where(Transaction.account_id == account_id)
    if txn_type:
        stmt = stmt.where(Transaction.type == txn_type)
        count_stmt = count_stmt.where(Transaction.type == txn_type)
    if currency:
        stmt = stmt.where(Transaction.currency == currency)
        count_stmt = count_stmt.where(Transaction.currency == currency)
    if date_from:
        stmt = stmt.where(Transaction.timestamp >= date_from)
        count_stmt = count_stmt.where(Transaction.timestamp >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.timestamp <= date_to)
        count_stmt = count_stmt.where(Transaction.timestamp <= date_to)

    sort_column = {"timestamp": Transaction.timestamp, "amount": Transaction.amount}.get(
        sort_by, Transaction.timestamp
    )
    stmt = stmt.order_by(asc(sort_column) if sort_dir == "asc" else desc(sort_column))
    stmt = stmt.offset((page - 1) * limit).limit(limit)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows), total
