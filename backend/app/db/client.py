"""MongoDB connection handling.

PyMongo 4.9 and later ship a native async client, so there is no need for Motor.
The client is created once at application startup and closed at shutdown; see the
lifespan handler in app/main.py.
"""

from __future__ import annotations

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import get_settings

_client: AsyncMongoClient | None = None


def get_client() -> AsyncMongoClient:
    """Return the process-wide client, creating it on first use."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncMongoClient(
            settings.mongodb_uri,
            # Fail fast on a bad URI rather than hanging a request for 30s.
            serverSelectionTimeoutMS=5000,
            tz_aware=True,
        )
    return _client


def get_db() -> AsyncDatabase:
    return get_client()[get_settings().mongodb_db]


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def ping() -> bool:
    """Cheap connectivity check used by the health endpoint."""
    await get_client().admin.command("ping")
    return True
