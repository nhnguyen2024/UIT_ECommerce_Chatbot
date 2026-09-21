"""Conversation persistence.

A session is one document holding an ordered list of messages. Turns append to
it, so the document grows over a conversation but never over the life of the
service.

Only the visible text, the citations, and the product cards are stored. Raw
tool_use blocks are deliberately not kept: they are large, they go stale within
minutes as prices and order statuses change, and the orchestrator does not
replay them anyway.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pymongo.errors import PyMongoError

from app.db import schema
from app.db.client import get_db

logger = logging.getLogger(__name__)

# How many past turns are replayed to the model. Long conversations stay cheap
# and the model keeps enough context to resolve references like "the second one".
HISTORY_TURNS = 12


async def get_history(session_id: str, limit: int = HISTORY_TURNS) -> list[dict]:
    """Return the most recent turns, oldest first, ready for replay.

    Never raises. Conversation history improves an answer but is not required to
    produce one, so a database outage degrades the assistant to single-turn
    replies rather than taking it offline. The alternative, letting the error
    propagate, turns a brief Atlas blip into a chatbot that answers nothing.
    """
    try:
        document = await get_db()[schema.CONVERSATIONS].find_one(
            {"session_id": session_id},
            # Slice server-side so a long conversation does not travel over the
            # wire in full just to have most of it discarded here.
            {"_id": 0, "lang": 1, "escalated": 1, "messages": {"$slice": -limit}},
        )
    except PyMongoError:
        logger.warning(
            "could not load history for session %s; continuing without it",
            session_id,
            exc_info=True,
        )
        return []

    if document is None:
        return []
    return [
        {"role": message["role"], "text": message.get("text", "")}
        for message in document.get("messages", [])
    ]


async def get_conversation(session_id: str) -> dict | None:
    return await get_db()[schema.CONVERSATIONS].find_one({"session_id": session_id}, {"_id": 0})


async def append_turn(
    *,
    session_id: str,
    lang: str,
    user_message: str,
    assistant_text: str,
    citations: list[dict],
    products: list[dict],
    tracking: list[dict] | None = None,
) -> None:
    """Append one user message and one assistant reply.

    Upserts, so the first turn of a session creates the document. `$setOnInsert`
    carries the fields that belong only to creation; setting `created_at` on
    every turn would reset it.
    """
    now = datetime.now(timezone.utc)

    user_entry = schema.StoredMessage(
        role="user", text=user_message, created_at=now
    ).model_dump()
    assistant_entry = schema.StoredMessage(
        role="assistant",
        text=assistant_text,
        citations=citations,
        products=products,
        tracking=tracking or [],
        created_at=now,
    ).model_dump()

    await get_db()[schema.CONVERSATIONS].update_one(
        {"session_id": session_id},
        {
            "$push": {"messages": {"$each": [user_entry, assistant_entry]}},
            "$set": {"lang": lang, "updated_at": now},
            "$setOnInsert": {"session_id": session_id, "created_at": now, "escalated": False},
        },
        upsert=True,
    )
