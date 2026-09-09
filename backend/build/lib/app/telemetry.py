"""Per-turn telemetry.

One event row per user turn. This is what the admin dashboard aggregates and
what makes cost and latency reportable rather than guessed at.

Writes are best effort. Losing a metrics row is an acceptable outcome; failing a
shopper's reply because a metrics write failed is not, so every error here is
swallowed after logging.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db import schema
from app.db.client import get_db

logger = logging.getLogger(__name__)


async def record_turn(*, session_id: str, outcome: dict) -> None:
    """Persist the outcome dict emitted by the orchestrator's `done` event."""
    try:
        event = schema.TurnEvent(
            session_id=session_id,
            at=datetime.now(timezone.utc),
            lang=outcome.get("lang", "vi"),
            intent=outcome.get("intent", "product_consultation"),
            tools_used=outcome.get("tools_used", []),
            tool_rounds=outcome.get("tool_rounds", 0),
            latency_ms=outcome.get("latency_ms", 0),
            input_tokens=outcome.get("input_tokens", 0),
            output_tokens=outcome.get("output_tokens", 0),
            cache_read_tokens=outcome.get("cache_read_tokens", 0),
            cache_write_tokens=outcome.get("cache_write_tokens", 0),
            cost_usd=outcome.get("cost_usd", 0.0),
            grounded=outcome.get("grounded", True),
            blocked=outcome.get("blocked", False),
            escalated=outcome.get("escalated", False),
            error=outcome.get("error"),
        )
        await get_db()[schema.EVENTS].insert_one(event.model_dump())
    except Exception:
        logger.exception("failed to record telemetry for session %s", session_id)
