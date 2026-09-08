"""
Sliding-window rate limiter backed by Redis sorted sets.

Why sliding window over a fixed window counter:
A fixed window (INCR + EXPIRE) allows up to 2x the limit at window
boundaries (e.g. 100 requests in the last second of one window and another
100 in the first second of the next). A sliding window log trims entries
older than the window on every check, so the limit holds no matter when a
client bursts.

Why a Lua script:
The check-and-increment must be atomic, or two API instances (or two
concurrent requests on the same instance) can race between "read count"
and "add entry" and both let a request through that should have been
rejected. Running the whole operation as one Lua script makes it a single
atomic unit on the Redis server regardless of how many API replicas are
calling it -- this is what makes the limiter correct under multiple API
instances.

Fail-open on Redis outage: if Redis is unreachable, requests are allowed
through rather than the whole API going down. This is a deliberate
availability-over-strictness trade-off, documented in README/ARCHITECTURE.
"""

import time

from app.redis_utils.client import get_redis

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window_ms)
local current = redis.call('ZCARD', key)

if current >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after_ms = window_ms
    if oldest[2] ~= nil then
        retry_after_ms = window_ms - (now - tonumber(oldest[2]))
    end
    return {0, current, retry_after_ms}
end

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window_ms)
return {1, current + 1, 0}
"""


class RateLimiter:
    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self.window_ms = 60_000
        self._redis = get_redis()
        self._script = self._redis.register_script(_SLIDING_WINDOW_LUA)

    async def check(self, client_id: str) -> tuple[bool, int, int]:
        """
        Returns (allowed, current_count, retry_after_ms).
        Fails open (allowed=True) if Redis is unavailable.
        """
        key = f"ratelimit:{client_id}"
        now_ms = int(time.time() * 1000)
        member = f"{now_ms}:{id(object())}"  # unique per call to avoid ZADD collisions
        try:
            allowed, count, retry_after_ms = await self._script(
                keys=[key], args=[now_ms, self.window_ms, self.limit, member]
            )
            return bool(allowed), int(count), int(retry_after_ms)
        except Exception:
            # Redis down -> fail open, documented trade-off.
            return True, 0, 0
