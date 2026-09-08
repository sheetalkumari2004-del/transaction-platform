from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import rate_limited_client
from app.db.session import get_db
from app.models.client import Client
from app.schemas.transaction_schema import TransactionOut, TransactionPage
from app.services.transaction_service import get_transaction, list_transactions

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


@router.get("", response_model=TransactionPage)
async def list_transactions_endpoint(
    account_id: str | None = None,
    type: str | None = Query(default=None, pattern="^(CREDIT|DEBIT)$"),
    currency: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
    sort_by: str = Query(default="timestamp", pattern="^(timestamp|amount)$"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    client: Client = Depends(rate_limited_client),
    db: AsyncSession = Depends(get_db),
):
    rows, total = await list_transactions(
        db, account_id, type, currency, date_from, date_to, page, limit, sort_by, sort_dir
    )
    return TransactionPage(
        items=[TransactionOut.model_validate(r) for r in rows], page=page, limit=limit, total=total
    )


@router.get("/{transaction_id}", response_model=TransactionOut)
async def get_transaction_endpoint(
    transaction_id: str,
    client: Client = Depends(rate_limited_client),
    db: AsyncSession = Depends(get_db),
):
    txn = await get_transaction(db, transaction_id)
    if txn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    return TransactionOut.model_validate(txn)
