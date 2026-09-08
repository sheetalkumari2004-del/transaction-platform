from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://txn_user:txn_pass@localhost:5432/txn_platform"
    database_url_sync: str = "postgresql+psycopg2://txn_user:txn_pass@localhost:5432/txn_platform"

    redis_url: str = "redis://localhost:6379/0"

    app_env: str = "local"
    log_level: str = "INFO"

    rate_limit_per_minute: int = 100
    account_summary_cache_ttl_seconds: int = 60

    import_stream_name: str = "import_jobs_stream"
    import_consumer_group: str = "import_workers"

    upload_max_bytes: int = 500 * 1024 * 1024  # 500 MB guard

    # How many rows the worker buffers before it does a bulk INSERT
    worker_batch_size: int = 1000
    # How many pending stream entries a worker claims from a dead consumer at once
    worker_claim_batch_size: int = 50
    # Idle time (ms) before a pending entry is considered abandoned and reclaimed
    worker_claim_idle_ms: int = 30_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
