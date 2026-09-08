"""
Account summary caching.

Key strategy: account:summary:{account_id} -> JSON blob of the summary
response. One key per account keeps invalidation trivial (a single DEL).

TTL: settings.account_summary_cache_ttl_seconds (default 60s). Short TTL
because summaries are derived aggregates that go stale the moment a new
transaction lands for that account; a short TTL bounds staleness for any
account we forget to explicitly invalidate.

Invalidation: write-through invalidation. Whenever a transaction is
inserted for an account (single insert or as part of a batch import), the
service layer deletes that account's cache key so the next read
recomputes from Postgres. TTL is the backstop for any path that misses
this.

Serialization: JSON (orjson) — summaries are small, flat dicts.

Redis-unavailable behavior: cache reads/writes fail open. On a cache miss
or Redis error, the summary is computed directly from Postgres, which
remains the source of truth throughout. The API stays correct, just
slower, if Redis is down.
"""

import orjson

from app.core.config import get_settings
from app.redis_utils.client import get_redis

settings = get_settings()


def _key(account_id: str) -> str:
    return f"account:summary:{account_id}"


async def get_account_summary_cache(account_id: str) -> dict | None:
    try:
        redis = get_redis()
        raw = await redis.get(_key(account_id))
        return orjson.loads(raw) if raw else None
    except Exception:
        return None


async def set_account_summary_cache(account_id: str, summary: dict) -> None:
    try:
        redis = get_redis()
        await redis.set(
            _key(account_id),
            orjson.dumps(summary),
            ex=settings.account_summary_cache_ttl_seconds,
        )
    except Exception:
        pass


async def invalidate_account_summary_cache(account_id: str) -> None:
    try:
        redis = get_redis()
        await redis.delete(_key(account_id))
    except Exception:
        pass
