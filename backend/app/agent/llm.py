"""Provider-neutral model interface.

The agent loop, the classifier and the judge need four things from a model:
stream a round of text, report the tool calls it asked for, accept the tool
results back, and return schema-validated structured output. This module offers
exactly those, over two wire formats:

    Anthropic Messages API   LLM_PROVIDER=anthropic, and cortex (Snowflake's
                             Anthropic-compatible endpoint)
    OpenAI Chat Completions  LLM_PROVIDER=azure_openai (Azure OpenAI, v1 API)

The formats disagree on almost every detail of tool use: Anthropic returns tool
calls as content blocks and takes results back as `tool_result` blocks in a
user message; OpenAI streams each call's JSON arguments in fragments that must be
reassembled by index and takes results back as `tool` role messages. Each
conversation class keeps its provider's native message list, so nothing is
translated back and forth between rounds, and callers see only the neutral types
below.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal, TypeVar

import anthropic
import openai
from pydantic import BaseModel

from app.agent.client import build_client, build_openai_client
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# "minimal" asks for the least reasoning a provider allows. Anthropic has no
# such level, and its smallest classifier model rejects effort outright, so the
# Anthropic backend sends nothing for it.
Effort = Literal["minimal", "low", "medium", "high"]

# Output ceiling for one agent round. Reasoning tokens count against it on
# OpenAI's reasoning models, so it is set well above the length of a reply.
AGENT_MAX_TOKENS = 8000

# Reasoning models spend completion tokens thinking before they write. A budget
# sized for the visible answer alone can be consumed entirely by reasoning,
# which returns an empty completion rather than an error.
OPENAI_REASONING_HEADROOM = 4000


@dataclass
class Usage:
    """Token counts for one model call, in one vocabulary for every provider.

    `input_tokens` excludes cached tokens: they are billed at a different rate,
    so cost estimation needs them apart.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # The model's arguments were not valid JSON. OpenAI streams them in
    # fragments, and a round cut short by the token limit can leave them
    # incomplete. The caller reports this to the model instead of running the
    # tool with invented arguments.
    malformed: bool = False


@dataclass
class ToolOutcome:
    call: ToolCall
    content: str
    is_error: bool


@dataclass
class RoundResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    refused: bool = False
    refusal_detail: str | None = None
    usage: Usage = field(default_factory=Usage)


class ModelError(Exception):
    """A model call failed. `code` goes to telemetry; `user_message` to the shopper."""

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(code)
        self.code = code
        self.user_message = user_message


def _unavailable(status: int) -> ModelError:
    return ModelError(f"api_error:{status}", "The assistant is unavailable right now.")


def _unreachable() -> ModelError:
    return ModelError("connection_error", "The assistant is unreachable right now.")


class ContentFiltered(ModelError):
    """The provider's own safety filter rejected the request before the model ran.

    Azure OpenAI screens every prompt (Prompt Shields, plus the hate, sexual,
    violence and self-harm classifiers) and answers a flagged one with HTTP 400
    instead of a completion. That is a verdict on the shopper's message, not an
    outage, so callers treat it as a refusal rather than "unavailable".
    """

    def __init__(self, categories: list[str]) -> None:
        self.categories = categories
        super().__init__(
            "content_filter:" + (",".join(categories) or "unspecified"),
            "The assistant declined to answer this request.",
        )


def content_filter_categories(exc: "openai.APIStatusError") -> list[str] | None:
    """The filtered categories if this error is a content-filter rejection, else None."""
    body = exc.body if isinstance(exc.body, dict) else {}
    if exc.status_code != 400 or body.get("code") != "content_filter":
        return None
    inner = body.get("innererror") or {}
    results = inner.get("content_filter_result") or {}
    return sorted(
        name
        for name, verdict in results.items()
        if isinstance(verdict, dict) and verdict.get("filtered")
    )


class Conversation(ABC):
    """One user turn's exchange with the model, across tool rounds."""

    @abstractmethod
    def stream_round(self) -> AsyncIterator[str | RoundResult]:
        """Yield text deltas as they arrive, then one final RoundResult."""

    @abstractmethod
    def add_tool_results(self, outcomes: list[ToolOutcome]) -> None:
        """Record the previous round's tool calls and their results."""


class ModelBackend(ABC):
    @abstractmethod
    def conversation(
        self, *, system: str, messages: list[dict], tools: list[dict], effort: Effort | None
    ) -> Conversation:
        """Start a turn.

        Args:
            system: The system prompt.
            messages: Plain-text history plus the new user message, as
                `{"role": "user" | "assistant", "content": str}` dicts.
            tools: Tool definitions in the registry's format (Anthropic's:
                name, description, input_schema, optional strict). Each backend
                converts them to its own wire format.
            effort: How hard the agent should think, or None to use the
                provider's default.
        """

    @abstractmethod
    async def parse(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int,
        effort: Effort | None = None,
    ) -> T | None:
        """Return structured output validated against `schema`.

        None when the model declines. Raises ModelError when the call fails.
        """


# --- Anthropic Messages API --------------------------------------------------


class AnthropicConversation(Conversation):
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        effort: Effort | None,
    ) -> None:
        self._client = client
        self._model = model
        # One cache breakpoint at the end of the system prompt. Tools render
        # before it in the cached prefix, and both are stable across requests,
        # so this covers everything that does not change between turns. Nothing
        # volatile may be added before it, or the cache is lost for every
        # conversation at once.
        self._system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self._messages = list(messages)
        self._tools = tools
        self._effort = {"output_config": {"effort": effort}} if effort and effort != "minimal" else {}
        self._last_content: list[Any] = []

    async def stream_round(self) -> AsyncIterator[str | RoundResult]:
        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=AGENT_MAX_TOKENS,
                system=self._system,
                messages=self._messages,
                tools=self._tools,
                thinking={"type": "adaptive"},
                **self._effort,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield event.delta.text
                response = await stream.get_final_message()
        except anthropic.APIStatusError as exc:
            logger.exception("Anthropic API error during turn")
            raise _unavailable(exc.status_code) from exc
        except anthropic.APIConnectionError as exc:
            logger.exception("Anthropic connection error during turn")
            raise _unreachable() from exc

        # Kept whole, thinking blocks included: the next round must send them
        # back unchanged for the model to continue its reasoning.
        self._last_content = list(response.content)
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        detail = getattr(response, "stop_details", None)
        yield RoundResult(
            text=text,
            tool_calls=[
                ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                for block in response.content
                if block.type == "tool_use"
            ],
            refused=response.stop_reason == "refusal",
            refusal_detail=getattr(detail, "category", None),
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
        )

    def add_tool_results(self, outcomes: list[ToolOutcome]) -> None:
        self._messages.append({"role": "assistant", "content": self._last_content})
        # Every result goes back in a single user message: splitting them
        # teaches the model to stop calling tools in parallel.
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": outcome.call.id,
                        "content": outcome.content,
                        "is_error": outcome.is_error,
                    }
                    for outcome in outcomes
                ],
            }
        )


class AnthropicBackend(ModelBackend):
    """Anthropic's API, or Snowflake Cortex's Anthropic-compatible endpoint."""

    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None) -> None:
        self._settings = settings
        self._client = client or build_client(settings)

    def conversation(self, *, system, messages, tools, effort):
        return AnthropicConversation(
            self._client,
            model=self._settings.agent_model,
            system=system,
            messages=messages,
            tools=tools,
            effort=effort if self._settings.llm_effort else None,
        )

    async def parse(self, *, model, system, user, schema, max_tokens, effort=None):
        extra: dict[str, Any] = {}
        if effort and effort != "minimal" and self._settings.llm_effort:
            extra["output_config"] = {"effort": effort}
        try:
            response = await self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                **extra,
            )
        except anthropic.APIStatusError as exc:
            raise _unavailable(exc.status_code) from exc
        except anthropic.APIConnectionError as exc:
            raise _unreachable() from exc
        return response.parsed_output


# --- OpenAI Chat Completions (Azure OpenAI) ---------------------------------


def openai_strict_compatible(schema: dict) -> bool:
    """Whether OpenAI's strict mode would accept this schema.

    OpenAI requires every property to be listed in `required`, with optional
    fields expressed as nullable instead, and `additionalProperties: false` on
    every object. The tool schemas here have genuinely optional filters, such as
    a price range, so most do not qualify. Sending `strict` for those would be a
    400 on every turn; leaving it off keeps the schema as guidance, and the
    orchestrator's TypeError guard covers what the guarantee would have.
    """
    if schema.get("type") != "object":
        return True
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is not False:
        return False
    if set(schema.get("required", [])) != set(properties):
        return False
    return all(openai_strict_compatible(child) for child in properties.values())


def to_openai_tools(tools: list[dict]) -> list[dict]:
    converted = []
    for tool in tools:
        function: dict[str, Any] = {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        }
        if tool.get("strict") and openai_strict_compatible(tool["input_schema"]):
            function["strict"] = True
        converted.append({"type": "function", "function": function})
    return converted


def openai_usage(usage: Any) -> Usage:
    if usage is None:
        return Usage()
    prompt = usage.prompt_tokens or 0
    details = getattr(usage, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    # OpenAI caches long prompt prefixes on its own. prompt_tokens already
    # includes the cached part, so it is subtracted to avoid charging it twice.
    return Usage(
        input_tokens=max(prompt - cached, 0),
        output_tokens=usage.completion_tokens or 0,
        cache_read_tokens=cached,
    )


class OpenAIConversation(Conversation):
    def __init__(
        self,
        client: openai.AsyncOpenAI,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        effort: Effort | None,
    ) -> None:
        self._client = client
        self._model = model
        self._messages: list[dict] = [{"role": "system", "content": system}, *messages]
        self._tools = to_openai_tools(tools)
        self._effort = effort
        self._pending_assistant: dict | None = None

    async def stream_round(self) -> AsyncIterator[str | RoundResult]:
        params: dict[str, Any] = {
            "model": self._model,
            "messages": self._messages,
            "stream": True,
            # Without this the stream carries no token counts at all.
            "stream_options": {"include_usage": True},
            "max_completion_tokens": AGENT_MAX_TOKENS,
        }
        if self._tools:
            params["tools"] = self._tools
        if self._effort:
            params["reasoning_effort"] = self._effort

        text_parts: list[str] = []
        refusal_parts: list[str] = []
        # Tool calls arrive as fragments keyed by index: the id and name in the
        # first fragment, the JSON arguments spread across many.
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage = None

        try:
            # The context manager closes the HTTP response even when the caller
            # stops reading early. Iterating the stream alone leaves the
            # connection open until garbage collection, which a long-running
            # server would slowly exhaust its connection pool on.
            async with await self._client.chat.completions.create(**params) as stream:
                async for chunk in stream:
                    if chunk.usage is not None:
                        usage = chunk.usage
                    for choice in chunk.choices:
                        delta = choice.delta
                        if delta.content:
                            text_parts.append(delta.content)
                            yield delta.content
                        if delta.refusal:
                            refusal_parts.append(delta.refusal)
                        for fragment in delta.tool_calls or []:
                            slot = calls.setdefault(
                                fragment.index, {"id": "", "name": "", "arguments": ""}
                            )
                            if fragment.id:
                                slot["id"] = fragment.id
                            if fragment.function is not None:
                                slot["name"] += fragment.function.name or ""
                                slot["arguments"] += fragment.function.arguments or ""
                        if choice.finish_reason:
                            finish_reason = choice.finish_reason
        except openai.APIStatusError as exc:
            categories = content_filter_categories(exc)
            if categories is not None:
                logger.info("Azure content filter rejected the turn: %s", categories)
                yield RoundResult(
                    text="",
                    tool_calls=[],
                    refused=True,
                    refusal_detail="content_filter:" + (",".join(categories) or "unspecified"),
                )
                return
            logger.exception("Azure OpenAI API error during turn")
            raise _unavailable(exc.status_code) from exc
        except openai.APIConnectionError as exc:
            logger.exception("Azure OpenAI connection error during turn")
            raise _unreachable() from exc

        tool_calls: list[ToolCall] = []
        for index in sorted(calls):
            slot = calls[index]
            try:
                arguments = json.loads(slot["arguments"] or "{}")
                malformed = not isinstance(arguments, dict)
            except json.JSONDecodeError:
                arguments, malformed = {}, True
            tool_calls.append(
                ToolCall(
                    id=slot["id"],
                    name=slot["name"],
                    arguments=arguments if isinstance(arguments, dict) else {},
                    malformed=malformed,
                )
            )

        text = "".join(text_parts)
        if tool_calls:
            # The next request must repeat the assistant's calls, with their
            # original argument strings, before the results that answer them.
            self._pending_assistant = {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {
                        "id": calls[index]["id"],
                        "type": "function",
                        "function": {"name": calls[index]["name"], "arguments": calls[index]["arguments"]},
                    }
                    for index in sorted(calls)
                ],
            }

        # Two ways to decline: an explicit refusal message, or Azure's content
        # filter stopping the completion.
        refused = bool(refusal_parts) or finish_reason == "content_filter"
        refusal_detail = None
        if refused:
            refusal_detail = "".join(refusal_parts) or "content_filter"

        yield RoundResult(
            text=text,
            tool_calls=tool_calls,
            refused=refused,
            refusal_detail=refusal_detail,
            usage=openai_usage(usage),
        )

    def add_tool_results(self, outcomes: list[ToolOutcome]) -> None:
        if self._pending_assistant is not None:
            self._messages.append(self._pending_assistant)
            self._pending_assistant = None
        for outcome in outcomes:
            # OpenAI has no error flag on a tool result. The payload of a failed
            # tool already carries an "error" key the model reads.
            self._messages.append(
                {"role": "tool", "tool_call_id": outcome.call.id, "content": outcome.content}
            )


class OpenAIBackend(ModelBackend):
    """Azure OpenAI through its OpenAI-compatible v1 API."""

    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None) -> None:
        self._settings = settings
        self._client = client or build_openai_client(settings)

    def conversation(self, *, system, messages, tools, effort):
        return OpenAIConversation(
            self._client,
            model=self._settings.agent_model,
            system=system,
            messages=messages,
            tools=tools,
            effort=effort if self._settings.llm_effort else None,
        )

    async def parse(self, *, model, system, user, schema, max_tokens, effort=None):
        params: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": schema,
            "max_completion_tokens": max_tokens + OPENAI_REASONING_HEADROOM,
        }
        if effort and self._settings.llm_effort:
            params["reasoning_effort"] = effort
        try:
            completion = await self._client.chat.completions.parse(**params)
        except openai.APIStatusError as exc:
            categories = content_filter_categories(exc)
            if categories is not None:
                raise ContentFiltered(categories) from exc
            raise _unavailable(exc.status_code) from exc
        except openai.APIConnectionError as exc:
            raise _unreachable() from exc
        message = completion.choices[0].message
        if message.refusal:
            return None
        return message.parsed


def build_backend(settings: Settings) -> ModelBackend:
    if settings.llm_provider == "azure_openai":
        return OpenAIBackend(settings)
    return AnthropicBackend(settings)


@lru_cache
def get_backend() -> ModelBackend:
    return build_backend(get_settings())
