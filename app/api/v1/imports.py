from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import rate_limited_client
from app.db.session import get_db
from app.models.client import Client
from app.models.import_job import ImportError as ImportErrorModel
from app.models.import_job import ImportJob
from app.schemas.import_schema import ImportCreateResponse, ImportErrorItem, ImportErrorPage, ImportStatusResponse
from app.services.import_service import create_import

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])


@router.post("", response_model=ImportCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_import_endpoint(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    client: Client = Depends(rate_limited_client),
    db: AsyncSession = Depends(get_db),
):
    import_job, _was_existing = await create_import(db, client, file, idempotency_key)
    return ImportCreateResponse(import_id=import_job.id, status=import_job.status.value)


@router.get("/{import_id}", response_model=ImportStatusResponse)
async def get_import_status(
    import_id: UUID,
    client: Client = Depends(rate_limited_client),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ImportJob).where(ImportJob.id == import_id, ImportJob.client_id == client.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
    return ImportStatusResponse(
        import_id=job.id,
        status=job.status.value,
        total_rows=job.total_rows,
        processed_rows=job.processed_rows,
        successful_rows=job.successful_rows,
        failed_rows=job.failed_rows,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/{import_id}/errors", response_model=ImportErrorPage)
async def get_import_errors(
    import_id: UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
    client: Client = Depends(rate_limited_client),
    db: AsyncSession = Depends(get_db),
):
    job_result = await db.execute(
        select(ImportJob.id).where(ImportJob.id == import_id, ImportJob.client_id == client.id)
    )
    if job_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")

    count_stmt = select(func.count()).select_from(ImportErrorModel).where(ImportErrorModel.import_job_id == import_id)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(ImportErrorModel)
        .where(ImportErrorModel.import_job_id == import_id)
        .order_by(ImportErrorModel.row_number)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()

    items = [
        ImportErrorItem(row=r.row_number, transaction_id=r.transaction_id, error=r.error) for r in rows
    ]
    return ImportErrorPage(items=items, page=page, limit=limit, total=total)
