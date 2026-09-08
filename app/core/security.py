import hashlib

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.client import Client


def hash_api_key(raw_key: str) -> str:
    # SHA-256 is sufficient here: API keys are high-entropy random tokens,
    # not low-entropy user passwords, so there's no offline brute-force
    # concern that would require a slow KDF like bcrypt/argon2.
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def get_current_client(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> Client:
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key header")

    key_hash = hash_api_key(x_api_key)
    result = await db.execute(select(Client).where(Client.api_key_hash == key_hash))
    client = result.scalar_one_or_none()

    if client is None or not client.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or inactive API key")

    return client
