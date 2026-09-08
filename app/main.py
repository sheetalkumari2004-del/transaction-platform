import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1 import accounts, health, imports, transactions
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger()

app = FastAPI(
    title="Transaction Processing Platform",
    description="Async CSV transaction ingestion, validation, querying and account summaries.",
    version="1.0.0",
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Attaches a request_id to every request for log correlation
    (assignment section 24: logs should include request_id)."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    response.headers["X-Request-ID"] = request_id
    logger.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round(duration_ms, 2),
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(health.router)
app.include_router(imports.router)
app.include_router(transactions.router)
app.include_router(accounts.router)


@app.get("/")
async def root():
    return {"service": "transaction-processing-platform", "docs": "/docs"}
