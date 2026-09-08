from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class TransactionOut(BaseModel):
    transaction_id: str
    account_id: str
    type: str
    amount: Decimal
    currency: str
    timestamp: datetime

    model_config = {"from_attributes": True}


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    page: int
    limit: int
    total: int


class AccountSummary(BaseModel):
    account_id: str
    total_credits: Decimal
    total_debits: Decimal
    transaction_count: int
    balance: Decimal
