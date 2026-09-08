from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ImportCreateResponse(BaseModel):
    import_id: UUID
    status: str


class ImportStatusResponse(BaseModel):
    import_id: UUID
    status: str
    total_rows: int | None
    processed_rows: int
    successful_rows: int
    failed_rows: int
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class ImportErrorItem(BaseModel):
    row: int
    transaction_id: str | None
    error: str


class ImportErrorPage(BaseModel):
    items: list[ImportErrorItem]
    page: int
    limit: int
    total: int
