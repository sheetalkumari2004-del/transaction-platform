from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import rate_limited_client
from app.db.session import get_db
from app.models.client import Client
from app.schemas.transaction_schema import AccountSummary
from app.services.account_service import get_account_summary

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


@router.get("/{account_id}/summary", response_model=AccountSummary)
async def get_account_summary_endpoint(
    account_id: str,
    client: Client = Depends(rate_limited_client),
    db: AsyncSession = Depends(get_db),
):
    summary = await get_account_summary(db, account_id)
    return AccountSummary(**summary)
