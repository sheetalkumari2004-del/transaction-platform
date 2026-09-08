# Architecture

## 1. System Architecture

```
                        Client (external system)
                              |
                              v
                    +-------------------+
                    |   FastAPI (API)   |  <-- stateless, horizontally scalable
                    +---------+---------+
                              |
              +---------------+----------------+
              |                                 |
              v                                 v
      +---------------+                 +----------------+
      |  PostgreSQL   |                 |     Redis      |
      |  - clients    |                 | - stream/PEL   |
      |  - import_jobs|                 | - rate limits  |
      |  - import_err |                 | - summary cache|
      |  - transactions|                +--------+-------+
      +---------------+                          |
                                                   v
                                          +-----------------+
                                          |  Worker process  |  <-- separately
                                          |  (consumer grp)  |      deployable/scalable
                                          +-----------------+
```

The API and worker are two processes built from the **same Docker image**
(`Dockerfile`) but run with different commands, so they're guaranteed to
run identical code while still being separately deployable/scalable
containers, as required. The API never touches the filesystem for
processing and never blocks on a CSV; it only accepts the upload, writes
it to shared storage, and hands off a job ID via Redis.

## 2. Database Schema

- **clients** — one row per external system; `api_key_hash` (SHA-256 of
  the raw key) is what's stored, never the raw key.
- **import_jobs** — one row per uploaded file. Tracks lifecycle status,
  row counters (`processed_rows`, `successful_rows`, `failed_rows`), and
  two fields that exist specifically for reliability:
  `idempotency_key` (unique) and `last_committed_row`.
- **import_errors** — one row per invalid CSV row, FK'd to `import_jobs`
  with `ON DELETE CASCADE`.
- **transactions** — the source of truth. `transaction_id` has a UNIQUE
  constraint — this is the actual duplicate-prevention mechanism, not
  application logic. `amount` is `NUMERIC(18,2)`, not float, to avoid
  binary-rounding errors in money.

**Indexes** (see `migrations/versions/0001_initial_schema.py`):
- `transactions.transaction_id` — unique + indexed, used by
  `GET /transactions/{id}` and by every `ON CONFLICT` upsert.
- `transactions(account_id, type, timestamp)` and
  `transactions(account_id, timestamp)` — composite indexes matching the
  actual filter/sort shape used by `GET /transactions` and the account
  summary aggregate, so neither degrades into a sequential scan as the
  table grows.
- `import_jobs.idempotency_key` — unique, enforces "same retried request
  never creates two jobs" at the database level, not just in application
  code (which can race).

## 3. Import Processing Flow

1. `POST /api/v1/imports` streams the upload to disk in 1MB chunks
   (never buffers the whole file in memory), hashing it incrementally.
2. An `import_jobs` row is created with `status=QUEUED` and an
   `idempotency_key` (client-supplied header, or a hash of the content
   scoped to the client if no header is given).
3. The job ID is `XADD`'d to a Redis Stream. The HTTP response returns
   immediately with `202 Accepted` — the API never waits for processing.
4. A worker `XREADGROUP`s the entry, loads the `ImportJob`, sets
   `status=PROCESSING`, and streams the CSV with `csv.DictReader` in
   batches of `worker_batch_size` (default 1000) rows.
5. Each batch is validated, bulk-inserted with `INSERT ... ON CONFLICT DO
   NOTHING`, and the job's progress counters + `last_committed_row` are
   committed to Postgres — not just at the end, but after every batch.
   This is what makes `GET /imports/{id}` show real-time progress and
   what makes a mid-file crash resumable (see §6).
6. When the file is exhausted, `status=COMPLETED` (or `FAILED` if the
   file itself is malformed, e.g. missing required columns).
7. The stream entry is only `XACK`'d after the whole file is processed.

## 4. Redis Usage

| Purpose | Mechanism | Details |
|---|---|---|
| Background queue | Streams + consumer group | `import_jobs_stream` / group `import_workers`. See §6. |
| Rate limiting | Sorted set, Lua script | Sliding window, 100 req/min/client, atomic across API instances. |
| Account summary cache | String, JSON | Key `account:summary:{account_id}`, TTL 60s, write-through invalidation. |

See `app/redis_utils/*.py` — every module has an in-file docstring
explaining its specific design rationale in depth.

## 5. Worker Architecture

One worker process = one Redis Stream consumer within the
`import_workers` group. Multiple worker replicas are safe to run
concurrently (`docker compose up --scale worker=3`, or Azure Container
Apps replica scaling) — Streams guarantee each stream entry (i.e. each
import job) is delivered to exactly one consumer at a time, with
automatic reassignment on failure (§6).

Within a single job, batches are processed sequentially (not
parallelized across batches) to keep `last_committed_row` a simple,
strictly-increasing checkpoint. Parallelizing within one file is a
reasonable future improvement (see README limitations) — for the 72-hour
scope, sequential-per-job with concurrent *jobs* (multiple files import
in parallel across workers) was the better use of time.

## 6. Failure / Recovery Strategy

This directly targets assignment §15: *"a worker failure should not
permanently lose an import."*

- **Redis Streams' Pending Entries List (PEL)** is the core mechanism.
  When a worker reads an entry via `XREADGROUP`, Redis moves it into that
  consumer's PEL and it stays there, *owned by that consumer*, until
  explicitly `XACK`'d. If the worker crashes before acking, the entry
  simply remains in the PEL — it is never lost.
- Any worker (including the crashed one after restart) periodically calls
  `XAUTOCLAIM` for entries idle longer than 30s (`worker_claim_idle_ms`).
  This reassigns ownership and redelivers the entry — this is the actual
  "restart recovers the work" behavior.
- **Resuming, not restarting**: because `last_committed_row` is persisted
  to Postgres after every batch, the worker re-reads the CSV from the
  start (cheap — it's just I/O, not re-validation or re-insertion) and
  skips rows up to `last_committed_row`, then continues from there. Rows
  already committed are never re-inserted or re-validated.
  - *Trade-off documented up front*: this resumes by row-count, not by
    byte offset. Byte-offset seeking would be faster to resume but is
    unsafe with CSV fields that contain embedded newlines inside quotes;
    row-count resume is correct in all cases, at the cost of a full
    re-read of the file up to the checkpoint. For the file sizes in scope
    here (500K rows), a re-read is a few seconds, not a real bottleneck.
- **Duplicate processing** (the same row processed twice, whether from a
  resumed job re-validating a row it turns out wasn't actually skipped,
  parallel workers momentarily double-claiming, or the same file
  re-imported) is absorbed by the **database unique constraint** via
  `ON CONFLICT DO NOTHING`. This is the final, authoritative guarantee —
  every other mechanism (idempotency keys, row-count checkpoints) is an
  optimization to avoid *unnecessary* duplicate work, not the correctness
  boundary itself.
- **Retry limit**: a job is retried up to `MAX_RETRIES=3` times (tracked
  via `import_jobs.retry_count`); beyond that it's marked `FAILED` with
  an error message rather than looping forever on a poison-pill file.
- **Redis itself being unavailable** is handled differently per use: rate
  limiting and caching fail open (correctness of the read path is
  preserved via Postgres; you just lose the optimization); the import
  queue has no such fallback since it *is* the processing mechanism — if
  Redis is down, new imports queue but don't process until it's back,
  which is documented as a known limitation.

## 7. Caching Strategy

Covered in depth in `app/redis_utils/cache.py`'s docstring. Summary:
key-per-account, 60s TTL, write-through invalidation (worker deletes the
account's cache key immediately after committing any transaction batch
that touches it, rather than waiting for TTL expiry), fail-open on
Redis errors, Postgres remains authoritative in all cases.

## 8. Azure Architecture

`deployment/main.bicep` provisions:
- One **Azure Container Apps Environment** (with Log Analytics attached
  for `az containerapp logs` / portal log queries).
- Two **Container Apps** — `txn-platform-api` (external ingress, HTTP
  autoscaling, liveness/readiness probes wired to `/health/live` and
  `/health/ready`) and `txn-platform-worker` (no ingress, fixed replica
  range).
- Postgres and Redis are **external, free-tier services** (Neon,
  Upstash) per assignment §28/30 rather than provisioned Azure resources
  — this avoids paid Azure Database/Cache SKUs and keeps the deployment
  reproducible without an Azure spending commitment.

Why Container Apps over AKS/App Service: the assignment explicitly
recommends it, and for two stateless containers plus an external DB/cache,
AKS's operational overhead (node pools, cluster upgrades) isn't justified
by the workload, and App Service's container support is more
web-app-shaped than "API + background worker" shaped. Container Apps
gives KEDA-based autoscaling (extendable to scale the worker on Redis
Stream depth, noted as a limitation/future improvement) with a much
smaller footprint to operate and explain in a 72-hour window.

## Five Important Technical Decisions and Their Trade-offs

1. **Redis Streams instead of Celery/RQ/SQS for the job queue.**
   *Trade-off*: Streams' consumer-group PEL gives crash recovery
   natively without a second broker abstraction, at the cost of writing
   the claim/ack loop by hand instead of using a mature library with
   built-in retry backoff policies, dead-letter queues, and scheduling.
   For this scope, the explicit control over redelivery semantics was
   worth more than the convenience.

2. **Row-count-based resume instead of byte-offset resume.**
   *Trade-off*: correctness (handles quoted multi-line CSV fields safely)
   over raw resume speed. A crash near the end of a 500K-row file means
   re-reading up to 500K lines of I/O before resuming — a few seconds,
   not the bottleneck at this scale, so correctness won.

3. **`ON CONFLICT DO NOTHING` as the sole authoritative dedup guarantee**,
   rather than a Redis-based "seen transaction_id" set checked before
   insert. *Trade-off*: a Redis pre-check would reduce failed-insert
   noise slightly, but would be a second source of truth that could drift
   from Postgres (e.g. if a transaction is later deleted) and would still
   need the DB constraint as a backstop anyway — so it was skipped as
   unnecessary complexity, not a missing optimization.

4. **One Docker image, two commands (API vs worker)**, instead of two
   separate Dockerfiles/images. *Trade-off*: slightly larger worker image
   (it carries FastAPI dependencies it doesn't use) in exchange for a
   structural guarantee that API and worker can never silently drift to
   different code versions — worth it given how easy that drift is to
   introduce accidentally with two build pipelines.

5. **Sliding-window rate limiting (sorted set + Lua) instead of a fixed
   window counter (`INCR`+`EXPIRE`).** *Trade-off*: a few more Redis
   operations per check (ZREMRANGEBYSCORE + ZCARD + ZADD vs a single
   INCR), in exchange for closing the boundary-burst loophole where a
   fixed window allows up to 2x the stated limit across a window edge —
   correctness of the "100/min" guarantee was judged worth the modest
   extra cost.
