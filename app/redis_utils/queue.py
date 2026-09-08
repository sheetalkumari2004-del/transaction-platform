"""
Background job queue built on Redis Streams + consumer groups.

Why Streams over Lists or a full task-queue library (Celery/RQ):
- A List-based queue (LPUSH/BRPOP) loses a job the instant a worker pops it
  and then crashes before finishing -- the item is gone from the list.
- Streams keep every claimed-but-unacked entry in a Pending Entries List
  (PEL) per consumer group. If a worker dies mid-processing, the entry
  simply sits in the PEL until another worker reclaims it with XAUTOCLAIM
  after an idle timeout. This gives at-least-once delivery "for free" from
  Redis itself, which is exactly the "worker crashes mid-import" scenario
  the assignment calls out.
- Streams also keep a full log (bounded via XTRIM/MAXLEN) which makes it
  easy to inspect what was enqueued, which Lists don't.
- Celery/RQ would add a second broker abstraction on top of the Redis the
  assignment already requires; Streams get the reliability properties
  using only redis-py, which is simpler to reason about and demo.

Duplicate jobs: XADD is called with a deterministic idempotency key
already checked at the API layer (see services/import_service.py) before
enqueueing, so a retried HTTP request never enqueues two jobs for the same
logical import. Even if it did, processing itself is idempotent (DB
UNIQUE constraint on transaction_id + ON CONFLICT DO NOTHING), so a
duplicate job is harmless, just wasted work.

Partially processed imports: the worker commits progress
(last_committed_row) to Postgres after every batch, not just at the end.
On restart it resumes from last_committed_row instead of re-reading rows
it already committed, and duplicate inserts within an already-processed
range are absorbed by the unique constraint regardless.
"""

from app.core.config import get_settings
from app.redis_utils.client import get_redis

settings = get_settings()


async def ensure_group() -> None:
    redis = get_redis()
    try:
        await redis.xgroup_create(
            name=settings.import_stream_name, groupname=settings.import_consumer_group, id="0", mkstream=True
        )
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def enqueue_import_job(import_job_id: str) -> str:
    redis = get_redis()
    entry_id = await redis.xadd(settings.import_stream_name, {"import_job_id": import_job_id})
    return entry_id


async def read_new_jobs(consumer_name: str, count: int = 1, block_ms: int = 5000):
    redis = get_redis()
    resp = await redis.xreadgroup(
        groupname=settings.import_consumer_group,
        consumername=consumer_name,
        streams={settings.import_stream_name: ">"},
        count=count,
        block=block_ms,
    )
    return _flatten(resp)


async def reclaim_stale_jobs(consumer_name: str, idle_ms: int, count: int = 50):
    """
    Recover entries that were claimed by a consumer that died before
    ack'ing them. This is the mechanism that satisfies "worker restart
    recovers in-flight work": any other worker instance calling this will
    pick up entries idle longer than idle_ms.
    """
    redis = get_redis()
    _cursor, entries, _deleted = await redis.xautoclaim(
        name=settings.import_stream_name,
        groupname=settings.import_consumer_group,
        consumername=consumer_name,
        min_idle_time=idle_ms,
        start_id="0-0",
        count=count,
    )
    return _flatten([(settings.import_stream_name, entries)]) if entries else []


async def ack_job(entry_id: str) -> None:
    redis = get_redis()
    await redis.xack(settings.import_stream_name, settings.import_consumer_group, entry_id)


def _flatten(resp):
    """redis-py xreadgroup/xautoclaim shape -> list of (entry_id, fields)."""
    out = []
    for _stream_name, entries in resp:
        for entry_id, fields in entries:
            out.append((entry_id, fields))
    return out
