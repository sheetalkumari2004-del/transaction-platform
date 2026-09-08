# Transaction Processing Platform

Async CSV transaction ingestion, validation, querying, and account
summaries. Built with FastAPI, PostgreSQL, Redis, and Docker; deployable
to Azure Container Apps. See `ARCHITECTURE.md` for design rationale.

## Local Setup

```bash
cp .env.example .env
docker compose up --build
```

This starts Postgres, Redis, runs Alembic migrations (`migrate` service,
runs once and exits), then starts the API (`:8000`) and one worker
replica. Swagger UI: http://localhost:8000/docs.

Create a client + API key (required — every endpoint except `/health/*`
needs `X-API-Key`):

```bash
docker compose exec api python -m scripts.create_client "My Test Client"
```

This prints the raw key once; only its SHA-256 hash is stored. Use it as:

```bash
curl -H "X-API-Key: <key>" http://localhost:8000/api/v1/transactions
```

Upload a file:

```bash
curl -X POST -H "X-API-Key: <key>" \
  -F "file=@transactions.csv" \
  http://localhost:8000/api/v1/imports
```

## Environment Configuration

See `.env.example`. Key variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Async Postgres URL (asyncpg), used by API/worker |
| `DATABASE_URL_SYNC` | Sync Postgres URL (psycopg2), used only by Alembic |
| `REDIS_URL` | Redis connection string |
| `RATE_LIMIT_PER_MINUTE` | Default 100, per client |
| `ACCOUNT_SUMMARY_CACHE_TTL_SECONDS` | Default 60 |
| `UPLOAD_MAX_BYTES` | Guard against unbounded uploads, default 500MB |

No secrets are committed; `.env` is gitignored.

## Database / Migrations

```bash
# Apply migrations
docker compose exec api alembic upgrade head

# Create a new migration after changing models/
docker compose exec api alembic revision --autogenerate -m "describe change"
```

Schema, indexes, and rationale are documented in `ARCHITECTURE.md` §2.

## Running Tests

```bash
docker compose -f docker-compose.test.yml up -d
export TEST_DATABASE_URL=postgresql+asyncpg://txn_user:txn_pass@localhost:5433/txn_platform_test
export TEST_REDIS_URL=redis://localhost:6380/0
export TESTING=1   # switches the DB pool to NullPool -- required so pooled
                    # connections don't get reused across pytest-asyncio's
                    # per-test event loops (see app/db/session.py)
pip install -r requirements.txt
pytest tests/                          # unit + API + concurrency + worker-recovery tests
```

The full end-to-end test (`tests/integration/test_full_import_flow.py`)
also needs an actual worker process consuming the stream:

```bash
UPLOAD_DIR=/tmp/uploads DATABASE_URL=$TEST_DATABASE_URL REDIS_URL=$TEST_REDIS_URL \
  python -m app.workers.import_worker &
RUN_WORKER_TESTS=1 pytest tests/integration/test_full_import_flow.py
```

Verified during development: this full stack (real Postgres 16, real
Redis, real API process, real worker process, no mocks) was actually run
end-to-end, repeatedly. Upload → async processing → duplicate/invalid-row
handling → status polling → paginated error listing → transaction
queries with filters → account summaries all produced exactly correct
results against live services. The rate limiter was confirmed to allow
exactly 100 requests/minute and reject the 101st with `429` plus correct
`Retry-After`/`X-RateLimit-*` headers. The full pytest suite (21 tests,
including the live end-to-end worker test) passes in under 5 seconds.

Test layout:
- `tests/unit/` — pure functions (CSV row validation), no external deps.
- `tests/integration/` — API tests (upload, status, errors, 404s,
  idempotency), plus worker-recovery tests that directly exercise the
  resume-from-checkpoint and retry-on-failure logic (`test_worker_recovery.py`)
  without needing to literally kill a process.
- `tests/concurrency/` — 10 simultaneous duplicate-transaction inserts,
  asserting exactly one row lands (`test_duplicate_transactions.py`).

## Background Processing

One worker process per `docker compose` `worker` replica; scale with
`docker compose up --scale worker=3`. Each worker is a consumer in a
Redis Streams consumer group — jobs are delivered to exactly one worker
at a time, with automatic recovery if a worker dies mid-job (see
`ARCHITECTURE.md` §6 for the full mechanism). Progress
(`processed_rows`/`successful_rows`/`failed_rows`/`last_committed_row`)
is committed to Postgres after every 1000-row batch, so
`GET /imports/{id}` reflects real progress and a crash resumes rather
than restarts.

## Redis

- **Background processing**: Streams + consumer group (`app/redis_utils/queue.py`).
- **Caching**: account summaries, key `account:summary:{account_id}`,
  60s TTL, invalidated on write (`app/redis_utils/cache.py`).
- **Rate limiting**: sliding-window sorted set + atomic Lua script, 100
  req/min/client by default, correct across multiple API replicas
  (`app/redis_utils/rate_limiter.py`).

Each module's docstring covers its specific trade-offs in more depth.

## Performance Testing

Two scripts under `load_test/`:

1. **Read-path load** (`locustfile.py`) — concurrent transaction queries
   and account summaries:
   ```bash
   pip install locust
   LOAD_TEST_API_KEY=<key> locust -f load_test/locustfile.py \
     --host http://localhost:8000 --users 100 --spawn-rate 10 \
     --run-time 60s --headless --csv=load_test/results/run1
   ```
   Reports requests/sec, p50/p95/p99 latency, and error rate directly in
   the Locust output and `run1_stats.csv`.

2. **Large-file import timing** (`time_large_import.py`):
   ```bash
   python load_test/generate_large_csv.py --rows 500000
   python load_test/time_large_import.py \
     --file load_test/large_transactions.csv --api-key <key>
   ```
   Prints upload time, end-to-end processing time, and effective
   rows/sec for the worker.

**Results, methodology, and analysis are NOT included in this
repository** — they must be generated by actually running the above
against a deployed instance and are expected to be captured live in the
walkthrough video (assignment §26/27F), since numbers from an
unrepresentative environment (this dev sandbox) would be
methodologically dishonest to present as real findings.

## Azure

See `ARCHITECTURE.md` §8 and `deployment/main.bicep`. Deployment is
scripted (not manual portal clicks) via `.github/workflows/deploy.yml`:
build image → push to ACR → run `alembic upgrade head` against the
production DB → deploy Container Apps via Bicep. Required GitHub secrets:
`AZURE_CREDENTIALS`, `AZURE_RESOURCE_GROUP`, `ACR_NAME`,
`ACR_LOGIN_SERVER`, `DATABASE_URL`, `DATABASE_URL_SYNC`, `REDIS_URL`.

**This repository has not been deployed to a live Azure environment as
part of this submission** — see Limitations below.

## Design Decisions

Top 5 documented in depth in `ARCHITECTURE.md` (Redis Streams over a task
queue library, row-count vs byte-offset resume, DB constraint as the sole
dedup authority, one image/two commands, sliding-window vs fixed-window
rate limiting). Also: NUMERIC(18,2) for money instead of float; SHA-256
(not bcrypt) for API keys since they're high-entropy random tokens, not
low-entropy passwords; fail-open behavior for rate limiting and caching
but not for the queue itself, since the queue's correctness *is* the
processing guarantee.

## Assumptions

- Currency validated against a fixed common-currency list (not the full
  ISO-4217 table) — see `app/services/validation.py`.
- Amounts limited to 2 decimal places; more precision is rejected rather
  than silently rounded.
- Timestamps accept ISO-8601 with `Z` or explicit offset; naive datetimes
  are treated as UTC.
- A CSV missing any required column fails the whole import immediately
  (can't be a per-row error, since the file itself is malformed).
- Within a single file, a repeated `transaction_id` is recorded as a
  per-row error the second time it's seen (not silently deduped) so the
  import-errors report reflects it.

## Limitations

Being direct about what's incomplete within the 72-hour scope, per
assignment §35/37:

- **No live Azure deployment or public URL** was created for this
  submission — the Bicep template, GitHub Actions workflow, and
  architecture are complete and intended to be deployable as-is, but
  actually running `az deployment group create` against a real
  subscription and free-tier Neon/Upstash instances wasn't done here.
- **No performance test results are included** — the scripts exist and
  are ready to run, but generating real numbers requires a running
  deployed (or at least fully up) instance; see "Performance Testing"
  above for why fabricated numbers weren't produced instead.
- **No walkthrough video.**
- Worker batches within one file are processed sequentially, not
  parallelized — multiple *files* import concurrently (across worker
  replicas), but one very large file is bounded by single-worker
  throughput. Parallelizing within a file (e.g. sharding row ranges) is a
  reasonable next step if a single 500K-row file needs to import faster
  than one worker manages.
- Worker autoscaling on Azure is a fixed replica range, not scaled on
  actual Redis Stream backlog depth. Container Apps supports a KEDA Redis
  Streams scaler for this; wiring it up was left for a follow-up given
  time constraints.
- No dead-letter queue for jobs that exceed `MAX_RETRIES` — they're
  marked `FAILED` with an error message but the underlying stream entry
  is acked and dropped rather than moved somewhere for manual inspection.
- Currency validation list is a documented assumption (see above), not
  the full ISO-4217 set.
