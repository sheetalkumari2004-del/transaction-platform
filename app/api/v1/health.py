from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.redis_utils.client import get_redis

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness():
    # Process is running and can respond -- no dependency checks here on
    # purpose, so a slow/degraded dependency doesn't get the pod killed by
    # an orchestrator's liveness probe.
    return {"status": "alive"}


@router.get("/ready")
async def readiness(db: AsyncSession = Depends(get_db)):
    checks = {"postgres": False, "redis": False}

    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = True
    except Exception:
        pass

    try:
        redis = get_redis()
        await redis.ping()
        checks["redis"] = True
    except Exception:
        pass

    ready = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
