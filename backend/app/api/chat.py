"""Chat endpoints.

The main endpoint is a POST that streams. Server-Sent Events are used rather
than WebSockets because the traffic is one-directional once a turn starts, and
SSE survives proxies and load balancers without special configuration.

Note for the frontend: the browser's built-in `EventSource` only issues GET
requests, so it cannot consume this endpoint. Read the response body as a stream
instead. See the Angular chat service for the working client.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from app.agent.orchestrator import run_turn
from app.db.conversations import append_turn, get_conversation, get_history
from app.telemetry import record_turn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None,
        description="Omit to start a new session. The response stream returns the id.",
    )


def _sse(event: dict) -> str:
    """Format one Server-Sent Event frame.

    `ensure_ascii=False` keeps Vietnamese readable on the wire instead of
    expanding every accented character into an escape sequence.
    """
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@router.post("/stream")
async def stream_chat(request: ChatRequest) -> StreamingResponse:
    """Run one turn, streaming progress and text as it is produced."""
    session_id = request.session_id or f"s_{uuid.uuid4().hex[:16]}"
    history = await get_history(session_id)

    async def generate():
        # The session id goes first so a client that started without one can
        # attach it to the conversation before any text arrives.
        yield _sse({"type": "session", "session_id": session_id})

        answer = ""
        citations: list[dict] = []
        products: list[dict] = []
        outcome: dict | None = None

        try:
            async for event in run_turn(
                session_id=session_id, user_message=request.message, history=history
            ):
                if event["type"] == "done":
                    answer = event["text"]
                    citations = event["citations"]
                    products = event["products"]
                    outcome = event["outcome"]
                yield _sse(event)
        except Exception:
            # The turn already streamed partial text, so the connection cannot
            # be turned into an HTTP error. Send a terminal error frame instead.
            logger.exception("chat turn failed for session %s", session_id)
            yield _sse({"type": "error", "message": "Something went wrong. Please try again."})
            return

        # Persistence happens after streaming so the shopper is never waiting on
        # a database write to see their answer.
        if outcome is not None:
            try:
                await append_turn(
                    session_id=session_id,
                    lang=outcome["lang"],
                    user_message=request.message,
                    assistant_text=answer,
                    citations=citations,
                    products=products,
                )
                await record_turn(session_id=session_id, outcome=outcome)
            except Exception:
                logger.exception("failed to persist turn for session %s", session_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tells nginx and similar proxies not to buffer, which would hold the
            # whole response back and defeat streaming entirely.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{session_id}")
async def read_conversation(session_id: str) -> dict:
    """Return a stored conversation so a reloaded page can restore it."""
    try:
        conversation = await get_conversation(session_id)
    except PyMongoError as exc:
        # 503 rather than 500: the service is fine, its datastore is not, and
        # the client should retry rather than treat the session as broken.
        logger.warning("conversation lookup failed for %s", session_id, exc_info=True)
        raise HTTPException(
            status_code=503, detail="Conversation store is unavailable"
        ) from exc

    if conversation is None:
        raise HTTPException(status_code=404, detail="No such session")
    return conversation
