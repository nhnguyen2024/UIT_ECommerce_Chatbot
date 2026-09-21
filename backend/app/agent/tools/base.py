"""Tool registry shared by every tool module.

A tool is a JSON schema the model sees plus an async handler the orchestrator
calls. Handlers receive a `ToolContext` alongside the model-supplied arguments.
The context carries things the model must not control, such as which language to
answer in and which session is asking. Keeping those out of the schema means the
model cannot be talked into changing them by a crafted user message.

Every handler returns a `ToolResult`. The `sources` list is what makes citation
possible: each entry is an identifier the model is instructed to quote, and the
orchestrator later converts those quotes into structured citations for the UI.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Language = Literal["vi", "en"]


@dataclass
class ToolContext:
    """Per-turn state handed to handlers. Never exposed to the model."""

    session_id: str
    lang: Language = "vi"


@dataclass
class ToolResult:
    """What a handler returns.

    Attributes:
        data: Payload serialised into the tool_result block the model reads.
        sources: Citable identifiers this result introduced, e.g.
            "return-policy#refund-timing" or "product:PHN-001".
        products: Product cards for the UI to render. Carried separately from
            `data` so the frontend does not have to parse the model's prose.
        is_error: Marks the tool_result block as an error for the model.
    """

    data: Any
    sources: list[str] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)
    is_error: bool = False


Handler = Callable[..., Awaitable[ToolResult]]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Handler

    def to_anthropic(self, *, strict: bool = True) -> dict:
        """Render the definition sent in the `tools` array.

        `strict` plus `additionalProperties: false` guarantees the arguments
        validate against the schema, so handlers can trust their inputs instead
        of defensively re-checking every field.

        `strict` can be switched off for a provider that rejects it. The schema
        still carries `additionalProperties: false` and `required`, so the model
        is still told the exact shape; what is lost is the guarantee, and the
        orchestrator's TypeError guard around each handler call covers that.
        """
        definition = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
        if strict:
            definition["strict"] = True
        return definition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def definitions(self, *, strict: bool = True) -> list[dict]:
        """Tool definitions in a stable order.

        Order matters for prompt caching: the tools block is rendered before the
        system prompt and messages, so reordering it invalidates the whole cached
        prefix on every request.
        """
        return [self._tools[name].to_anthropic(strict=strict) for name in sorted(self._tools)]

    def names(self) -> list[str]:
        return sorted(self._tools)


registry = ToolRegistry()


def tool(*, name: str, description: str, input_schema: dict) -> Callable[[Handler], Handler]:
    """Register a handler as a tool and return it unchanged.

    Returning the original function keeps handlers directly unit testable: tests
    call them without going near the model or the registry.
    """

    def decorator(handler: Handler) -> Handler:
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                input_schema=input_schema,
                handler=handler,
            )
        )
        return handler

    return decorator
