"""
Usage: python -m scripts.create_client "My Client Name"

Prints the raw API key ONCE. Only the SHA-256 hash is stored in Postgres.
"""

import asyncio
import secrets
import sys

from app.core.security import hash_api_key
from app.db.session import AsyncSessionLocal
from app.models.client import Client


async def main(name: str) -> None:
    raw_key = f"tp_{secrets.token_urlsafe(32)}"
    async with AsyncSessionLocal() as db:
        client = Client(name=name, api_key_hash=hash_api_key(raw_key))
        db.add(client)
        await db.commit()
        await db.refresh(client)

    print(f"Client created: {client.id} ({name})")
    print(f"API key (save this now, it will not be shown again):\n{raw_key}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m scripts.create_client <client_name>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
