from fastapi import Depends, HTTPException, Response, status

from app.core.config import get_settings
from app.core.security import get_current_client
from app.models.client import Client
from app.redis_utils.rate_limiter import RateLimiter

settings = get_settings()
_limiter = RateLimiter(limit_per_minute=settings.rate_limit_per_minute)


async def rate_limited_client(response: Response, client: Client = Depends(get_current_client)) -> Client:
    allowed, count, retry_after_ms = await _limiter.check(str(client.id))

    if not allowed:
        # Headers set on the injected `Response` are silently dropped when
        # an HTTPException is raised instead of a normal return -- FastAPI
        # builds a fresh response for exceptions. They must be passed
        # explicitly via HTTPException(headers=...) to actually reach the
        # client on a 429.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded: 100 requests per minute per client",
            headers={
                "Retry-After": str(max(1, retry_after_ms // 1000)),
                "X-RateLimit-Limit": str(settings.rate_limit_per_minute),
                "X-RateLimit-Remaining": "0",
            },
        )

    response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
    response.headers["X-RateLimit-Remaining"] = str(max(0, settings.rate_limit_per_minute - count))
    return client
