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

from app.agent.classifier import classify_turn
from app.agent.guardrails import (
    DECLINED_REPLY,
    check_citations,
    check_numeric_grounding,
    screen_input,
)
from app.agent.llm import ModelError, RoundResult, ToolCall, ToolOutcome, Usage, get_backend
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

    def add(self, usage: Usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read_tokens += usage.cache_read_tokens
        self.cache_write_tokens += usage.cache_write_tokens

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
    # The same identifiers as `sources`, but in the order the tools returned
    # them. Citation validation only needs membership, so a set is enough there;
    # measuring retrieval quality needs rank, because mean reciprocal rank is
    # defined over an ordered list.
    retrieved: list[str] = field(default_factory=list)
    tool_payloads: list[Any] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)
    tracking: list[dict] = field(default_factory=list)
    usage: UsageTotals = field(default_factory=UsageTotals)
    escalated: bool = False
    blocked: bool = False
    error: str | None = None


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


# (payload, is_error, sources, product cards, tracking)
ToolRun = tuple[Any, bool, list[str], list[dict], dict | None]


async def _execute_tool(name: str, arguments: dict, context: ToolContext) -> ToolRun:
    """Run one tool. Returns (payload, is_error, sources, product cards, tracking)."""
    spec = registry.get(name)
    if spec is None:
        # The model can only call tools we defined, so this means a registry bug.
        return {"error": f"Unknown tool {name!r}."}, True, [], [], None

    try:
        result = await spec.handler(context=context, **arguments)
    except TypeError:
        # Arguments did not match the handler signature. With strict schemas this
        # is unreachable; without them (see openai_strict_compatible in llm.py)
        # it is the guard that turns a bad call into an error the model can read.
        logger.exception("tool %s rejected its arguments", name)
        return {"error": f"Tool {name!r} could not be called with those arguments."}, True, [], [], None
    except Exception:
        logger.exception("tool %s failed", name)
        return {"error": f"Tool {name!r} failed. Do not retry it this turn."}, True, [], [], None

    return result.data, result.is_error, result.sources, result.products, result.tracking


async def _run_call(call: ToolCall, context: ToolContext) -> ToolRun:
    if call.malformed:
        # Arguments that were not valid JSON, usually cut off by the output
        # limit. Running the tool on a guess could answer the wrong question;
        # telling the model lets it call again properly.
        return {"error": f"The arguments for {call.name!r} were not valid JSON. Call it again."}, True, [], [], None
    return await _execute_tool(call.name, call.arguments, context)


REPLY_LANGUAGE = {
    "vi": "The shopper's latest message is in Vietnamese. Reply entirely in Vietnamese.",
    "en": "The shopper's latest message is in English. Reply entirely in English.",
}


def system_prompt_for(lang: str) -> str:
    """The system prompt with this turn's reply language stated outright.

    The prompt's own examples are Vietnamese, and the first eval run caught the
    model answering an English complaint in Vietnamese despite the "mirror the
    shopper" rule. The classifier had labelled the language correctly, so it is
    passed on. It goes last, so the long shared prefix stays cacheable.
    """
    return f"{SYSTEM_PROMPT}\n\n# This turn\n\n{REPLY_LANGUAGE['vi' if lang == 'vi' else 'en']}"


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
    conversation = get_backend().conversation(
        system=system_prompt_for(state.lang),
        messages=build_messages(history, user_message),
        tools=registry.definitions(strict=settings.llm_strict_tools),
        effort=settings.agent_effort,  # type: ignore[arg-type]
    )
    answer_parts: list[str] = []

    try:
        for round_index in range(settings.max_tool_rounds):
            state.tool_rounds = round_index + 1

            result: RoundResult | None = None
            async for item in conversation.stream_round():
                if isinstance(item, RoundResult):
                    result = item
                else:
                    answer_parts.append(item)
                    yield {"type": "text", "delta": item}
            assert result is not None, "stream_round must end with a RoundResult"

            state.usage.add(result.usage)

            if result.refused:
                # A refusal is an answer, not a failure: the shopper gets a
                # short decline in their language and the usual redirect. Any
                # partial text is dropped, since a refusal can arrive mid-reply;
                # the done event's text replaces whatever was streamed.
                logger.info("turn refused for session %s: %s", session_id, result.refusal_detail)
                state.blocked = True
                reply = DECLINED_REPLY["vi" if state.lang == "vi" else "en"]
                if not answer_parts:
                    yield {"type": "text", "delta": reply}
                yield _done_event(
                    text=reply,
                    citations=[],
                    state=state,
                    started=started,
                    block_reason=f"refusal:{result.refusal_detail}",
                )
                return

            if not result.tool_calls:
                break

            for call in result.tool_calls:
                yield {"type": "tool_start", "name": call.name, "input": call.arguments}

            # Tools in one round are independent, so they run concurrently.
            results = await asyncio.gather(
                *(_run_call(call, context) for call in result.tool_calls)
            )

            outcomes: list[ToolOutcome] = []
            for call, (payload, is_error, sources, products, tracking) in zip(result.tool_calls, results):
                state.tools_used.append(call.name)
                state.sources.update(sources)
                for source in sources:
                    if source not in state.retrieved:
                        state.retrieved.append(source)
                state.tool_payloads.append(payload)
                state.products.extend(products)
                if tracking is not None:
                    state.tracking.append(tracking)
                if call.name == "create_handoff" and not is_error:
                    state.escalated = True

                outcomes.append(
                    ToolOutcome(
                        call=call,
                        content=json.dumps(payload, ensure_ascii=False, default=str),
                        is_error=is_error,
                    )
                )
                yield {"type": "tool_end", "name": call.name, "ok": not is_error}

            conversation.add_tool_results(outcomes)
        else:
            # Loop ran to its bound without the model settling on an answer.
            logger.warning("turn hit the tool round limit for session %s", session_id)
            state.error = "tool_round_limit"

    except ModelError as exc:
        state.error = exc.code
        yield {"type": "error", "message": exc.user_message}
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

    if state.tracking:
        yield {"type": "tracking", "items": state.tracking}
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
        "tracking": state.tracking,
        "outcome": {
            "lang": state.lang,
            "intent": state.intent,
            "confidence": state.confidence,
            "tools_used": state.tools_used,
            "retrieved_sources": list(state.retrieved),
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
