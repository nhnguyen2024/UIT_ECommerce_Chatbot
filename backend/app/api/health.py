"""Health and readiness endpoints.

Azure Container Apps probes these. `/health` answers without touching anything
external so a slow database cannot cause the container to be restarted, while
`/ready` reports whether dependencies are actually reachable.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.agent.tools import registry
from app.config import get_settings
from app.db.client import ping

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict:
    settings = get_settings()
    try:
        await ping()
        database = "ok"
    except Exception as exc:  # surfaced to the operator, not to a shopper
        database = f"unreachable: {type(exc).__name__}"

    return {
        "database": database,
        "agent_model": settings.agent_model,
        "classifier_model": settings.classifier_model,
        "embedding_mode": settings.embedding_mode,
        "tools": registry.names(),
    }
