"""The agent loop.

One user turn goes through: classify, screen, then a bounded loop of model call
and tool execution until the model stops asking for tools, then citation and
grounding checks over the finished answer.

The loop is written out here rather than delegated to the SDK's tool runner for
three reasons. The runner keeps its own copy of message history and does not
expose it, so a chat app that persists conversations has to mirror history
anyway. Progress events have to be emitted mid-turn so the interface can show
which tool is running. And the runner is a beta surface, which is a poor
dependency for a system that has to keep working on the day it is demonstrated.

Events are yielded as dicts for the transport layer to serialise. The final
`done` event carries everything the caller needs to persist the turn, so the
caller never has to reach back into the loop's internals.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import anthropic

from app.agent.classifier import classify_turn
from app.agent.client import get_anthropic
from app.agent.guardrails import check_citations, check_numeric_grounding, screen_input
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import ToolContext, registry
from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class UsageTotals:
    """Token counts summed across every model call in one turn."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, usage: Any) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def cost_usd(self) -> float:
        settings = get_settings()
        million = 1_000_000
        return round(
            self.input_tokens / million * settings.price_input_per_mtok
            + self.output_tokens / million * settings.price_output_per_mtok
            + self.cache_read_tokens / million * settings.price_cache_read_per_mtok
            # Writing to cache costs about 1.25x the input rate.
            + self.cache_write_tokens / million * settings.price_input_per_mtok * 1.25,
            6,
        )


@dataclass
class TurnState:
    """Accumulates everything observed while a turn runs."""

    lang: str = "vi"
    intent: str = "product_consultation"
    confidence: float = 0.0
    tools_used: list[str] = field(default_factory=list)
    tool_rounds: int = 0
    sources: set[str] = field(default_factory=set)
    tool_payloads: list[Any] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)
    usage: UsageTotals = field(default_factory=UsageTotals)
    escalated: bool = False
    blocked: bool = False
    error: str | None = None


def build_system_blocks() -> list[dict]:
    """System prompt with a cache breakpoint at the end.

    Tools render before the system prompt in the cached prefix, and both are
    stable across requests, so one breakpoint here covers everything that does
    not change between turns. Nothing volatile may be added before this point or
    the cache is invalidated for every conversation at once.
    """
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def build_messages(history: list[dict], user_message: str) -> list[dict]:
    """Replay prior turns as plain text.

    Tool calls and their results are not replayed. Keeping them would grow the
    context by a large multiple for little benefit: the model can call a tool
    again if it needs the data, and stale retrieved content is worse than none,
    because prices and order statuses change between turns.
    """
    messages: list[dict] = []
    for turn in history:
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        role = "user" if turn.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": text})

    messages.append({"role": "user", "content": user_message})
    return messages


async def _execute_tool(name: str, arguments: dict, context: ToolContext) -> tuple[Any, bool, list[str], list[dict]]:
    """Run one tool. Returns (payload, is_error, sources, product cards)."""
    spec = registry.get(name)
    if spec is None:
        # The model can only call tools we defined, so this means a registry bug.
        return {"error": f"Unknown tool {name!r}."}, True, [], []

    try:
        result = await spec.handler(context=context, **arguments)
    except TypeError:
        # Arguments did not match the handler signature. With strict schemas this
        # should be unreachable, so it signals a schema and handler mismatch.
        logger.exception("tool %s rejected its arguments", name)
        return {"error": f"Tool {name!r} could not be called with those arguments."}, True, [], []
    except Exception:
        logger.exception("tool %s failed", name)
        return {"error": f"Tool {name!r} failed. Do not retry it this turn."}, True, [], []

    return result.data, result.is_error, result.sources, result.products


async def run_turn(
    *,
    session_id: str,
    user_message: str,
    history: list[dict] | None = None,
) -> AsyncIterator[dict]:
    """Run one user turn, yielding progress events.

    Yields:
        Event dicts. `text` events carry incremental output. The terminal event
        is either `done`, carrying the full outcome, or `error`.
    """
    settings = get_settings()
    history = history or []
    state = TurnState()
    started = time.perf_counter()

    # --- Classify and screen ------------------------------------------------
    classification = await classify_turn(user_message, history)
    state.lang = classification.language
    state.intent = classification.intent
    state.confidence = classification.confidence

    yield {
        "type": "start",
        "lang": state.lang,
        "intent": state.intent,
        "confidence": state.confidence,
    }

    verdict = screen_input(
        safety=classification.safety,
        intent=classification.intent,
        confidence=classification.confidence,
        lang=state.lang,
    )
    if not verdict.allowed:
        state.blocked = True
        yield {"type": "text", "delta": verdict.canned_reply}
        yield _done_event(
            text=verdict.canned_reply or "",
            citations=[],
            state=state,
            started=started,
            block_reason=verdict.reason,
        )
        return

    # --- Model and tool loop ------------------------------------------------
    context = ToolContext(session_id=session_id, lang=state.lang)  # type: ignore[arg-type]
    messages = build_messages(history, user_message)
    tools = registry.definitions()
    answer_parts: list[str] = []

    try:
        for round_index in range(settings.max_tool_rounds):
            state.tool_rounds = round_index + 1

            async with get_anthropic().messages.stream(
                model=settings.agent_model,
                max_tokens=8000,
                system=build_system_blocks(),
                messages=messages,
                tools=tools,
                thinking={"type": "adaptive"},
                output_config={"effort": settings.agent_effort},
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        answer_parts.append(event.delta.text)
                        yield {"type": "text", "delta": event.delta.text}

                response = await stream.get_final_message()

            state.usage.add(response.usage)

            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                state.error = f"refusal:{getattr(detail, 'category', None)}"
                yield {
                    "type": "error",
                    "message": "The assistant declined to answer this request.",
                }
                yield _done_event(
                    text="".join(answer_parts), citations=[], state=state, started=started
                )
                return

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                break

            messages.append({"role": "assistant", "content": response.content})

            for block in tool_uses:
                yield {"type": "tool_start", "name": block.name, "input": block.input}

            # Tools in one assistant message are independent, so they run
            # concurrently. Every result must go back in a single user message:
            # splitting them teaches the model to stop calling tools in parallel.
            results = await asyncio.gather(
                *(_execute_tool(block.name, dict(block.input), context) for block in tool_uses)
            )

            tool_result_blocks = []
            for block, (payload, is_error, sources, products) in zip(tool_uses, results):
                state.tools_used.append(block.name)
                state.sources.update(sources)
                state.tool_payloads.append(payload)
                state.products.extend(products)
                if block.name == "create_handoff" and not is_error:
                    state.escalated = True

                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(payload, ensure_ascii=False, default=str),
                        "is_error": is_error,
                    }
                )
                yield {"type": "tool_end", "name": block.name, "ok": not is_error}

            messages.append({"role": "user", "content": tool_result_blocks})
        else:
            # Loop ran to its bound without the model settling on an answer.
            logger.warning("turn hit the tool round limit for session %s", session_id)
            state.error = "tool_round_limit"

    except anthropic.APIStatusError as exc:
        logger.exception("Anthropic API error during turn")
        state.error = f"api_error:{exc.status_code}"
        yield {"type": "error", "message": "The assistant is unavailable right now."}
        yield _done_event(text="".join(answer_parts), citations=[], state=state, started=started)
        return
    except anthropic.APIConnectionError:
        logger.exception("Anthropic connection error during turn")
        state.error = "connection_error"
        yield {"type": "error", "message": "The assistant is unreachable right now."}
        yield _done_event(text="".join(answer_parts), citations=[], state=state, started=started)
        return

    # --- Post-answer checks -------------------------------------------------
    raw_text = "".join(answer_parts)
    citation_check = check_citations(raw_text, state.sources)
    grounding = check_numeric_grounding(citation_check.text, state.tool_payloads)

    if citation_check.invalid:
        logger.warning(
            "session %s produced %d citation(s) to sources no tool returned: %s",
            session_id,
            len(citation_check.invalid),
            citation_check.invalid,
        )

    if state.products:
        yield {"type": "products", "items": _dedupe_products(state.products)}
    if citation_check.citations:
        yield {"type": "citations", "items": citation_check.citations}

    yield _done_event(
        text=citation_check.text,
        citations=citation_check.citations,
        state=state,
        started=started,
        grounded=grounding.grounded,
        unsupported=grounding.unsupported,
        invalid_citations=citation_check.invalid,
    )


def _dedupe_products(products: list[dict]) -> list[dict]:
    """One card per SKU, keeping first-seen order.

    A turn that searches and then fetches details returns the same product
    twice, and the interface should not render it twice.
    """
    seen: set[str] = set()
    unique: list[dict] = []
    for product in products:
        sku = product.get("sku")
        if sku and sku not in seen:
            seen.add(sku)
            unique.append(product)
    return unique


def _done_event(
    *,
    text: str,
    citations: list[dict],
    state: TurnState,
    started: float,
    grounded: bool = True,
    unsupported: list[str] | None = None,
    invalid_citations: list[str] | None = None,
    block_reason: str | None = None,
) -> dict:
    return {
        "type": "done",
        "text": text,
        "citations": citations,
        "products": _dedupe_products(state.products),
        "outcome": {
            "lang": state.lang,
            "intent": state.intent,
            "confidence": state.confidence,
            "tools_used": state.tools_used,
            "tool_rounds": state.tool_rounds,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "input_tokens": state.usage.input_tokens,
            "output_tokens": state.usage.output_tokens,
            "cache_read_tokens": state.usage.cache_read_tokens,
            "cache_write_tokens": state.usage.cache_write_tokens,
            "cost_usd": state.usage.cost_usd(),
            "grounded": grounded,
            "unsupported_figures": unsupported or [],
            "invalid_citations": invalid_citations or [],
            "blocked": state.blocked,
            "block_reason": block_reason,
            "escalated": state.escalated,
            "error": state.error,
        },
    }
