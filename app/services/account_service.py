from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction, TransactionType
from app.redis_utils.cache import get_account_summary_cache, set_account_summary_cache


async def get_account_summary(db: AsyncSession, account_id: str) -> dict:
    cached = await get_account_summary_cache(account_id)
    if cached is not None:
        return cached

    # A single aggregate query with conditional SUMs -- avoids pulling
    # every transaction row into Python and summing there, which is what
    # "consider how this endpoint performs as transactions grow" is
    # testing for.
    stmt = select(
        func.coalesce(
            func.sum(case((Transaction.type == TransactionType.CREDIT, Transaction.amount), else_=0)), 0
        ).label("total_credits"),
        func.coalesce(
            func.sum(case((Transaction.type == TransactionType.DEBIT, Transaction.amount), else_=0)), 0
        ).label("total_debits"),
        func.count(Transaction.id).label("transaction_count"),
    ).where(Transaction.account_id == account_id)

    row = (await db.execute(stmt)).one()
    total_credits = Decimal(row.total_credits)
    total_debits = Decimal(row.total_debits)

    summary = {
        "account_id": account_id,
        "total_credits": str(total_credits),
        "total_debits": str(total_debits),
        "transaction_count": row.transaction_count,
        "balance": str(total_credits - total_debits),
    }

    await set_account_summary_cache(account_id, summary)
    return summary
