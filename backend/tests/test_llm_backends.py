"""The provider-neutral model layer, checked against fake streams.

No request is sent. The OpenAI-format tests feed hand-built chunks through the
real stream handling, because the details that break in production are exactly
the ones a live smoke test rarely exercises: tool-call arguments split across
several chunks, two tool calls interleaved by index, arguments cut off
mid-JSON, a content-filter stop, and cached tokens double-counted in usage.
"""

from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionChunk
from pydantic import ValidationError

from app.agent.client import azure_openai_base_url
from app.agent.llm import (
    AnthropicBackend,
    AnthropicConversation,
    OpenAIBackend,
    OpenAIConversation,
    RoundResult,
    ToolCall,
    ToolOutcome,
    build_backend,
    openai_strict_compatible,
    openai_usage,
    to_openai_tools,
)
from app.agent.tools import registry
from app.config import Settings

ENDPOINT = "https://res.openai.azure.com"


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def azure(**overrides) -> Settings:
    return settings(
        llm_provider="azure_openai",
        azure_openai_endpoint=ENDPOINT,
        azure_openai_api_key="key",
        **overrides,
    )


# --- Fake OpenAI stream -----------------------------------------------------


def chunk(delta: dict | None = None, finish: str | None = None, usage: dict | None = None):
    choices = [] if delta is None and finish is None else [
        {"index": 0, "delta": delta or {}, "finish_reason": finish}
    ]
    return ChatCompletionChunk.model_validate(
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-5-mini",
            "choices": choices,
            "usage": usage,
        }
    )


def tool_fragment(index: int, *, id: str | None = None, name: str | None = None, args: str = ""):
    function = {"arguments": args}
    if name:
        function["name"] = name
    fragment = {"index": index, "function": function, "type": "function"}
    if id:
        fragment["id"] = id
    return {"tool_calls": [fragment]}


class FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self._chunks:
            yield item


class FakeCompletions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.requests: list[dict] = []
        self.stream: FakeStream | None = None

    async def create(self, **params):
        # A deep enough copy that later appends to the conversation don't
        # rewrite what this request actually sent.
        self.requests.append({**params, "messages": [dict(m) for m in params["messages"]]})
        self.stream = FakeStream(self.chunks)
        return self.stream


def fake_client(chunks):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(chunks)))


async def run(conversation) -> tuple[list[str], RoundResult]:
    deltas, result = [], None
    async for item in conversation.stream_round():
        if isinstance(item, RoundResult):
            result = item
        else:
            deltas.append(item)
    return deltas, result


def conversation_for(chunks, *, tools=None, effort=None):
    client = fake_client(chunks)
    conversation = OpenAIConversation(
        client,
        model="gpt-5-mini",
        system="SYSTEM",
        messages=[{"role": "user", "content": "hi"}],
        tools=tools or [],
        effort=effort,
    )
    return conversation, client.chat.completions


# --- OpenAI stream handling --------------------------------------------------


class TestOpenAIStream:
    async def test_text_is_streamed_and_collected(self):
        conversation, _ = conversation_for(
            [chunk({"content": "Xin "}), chunk({"content": "chào"}), chunk(finish="stop")]
        )
        deltas, result = await run(conversation)
        assert deltas == ["Xin ", "chào"]
        assert result.text == "Xin chào"
        assert result.tool_calls == []
        assert result.refused is False

    async def test_tool_call_arguments_are_reassembled_across_chunks(self):
        conversation, _ = conversation_for(
            [
                chunk(tool_fragment(0, id="call_1", name="search_products", args='{"que')),
                chunk(tool_fragment(0, args='ry": "tai nghe", "max_')),
                chunk(tool_fragment(0, args='price": 2000000}')),
                chunk(finish="tool_calls"),
            ]
        )
        _, result = await run(conversation)
        assert result.tool_calls == [
            ToolCall(
                id="call_1",
                name="search_products",
                arguments={"query": "tai nghe", "max_price": 2000000},
            )
        ]

    async def test_interleaved_parallel_calls_are_kept_apart_by_index(self):
        conversation, _ = conversation_for(
            [
                chunk(tool_fragment(0, id="a", name="get_product_details", args='{"sku": ')),
                chunk(tool_fragment(1, id="b", name="get_product_details", args='{"sku": "LAP-002"}')),
                chunk(tool_fragment(0, args='"PHN-001"}')),
                chunk(finish="tool_calls"),
            ]
        )
        _, result = await run(conversation)
        assert [(c.id, c.arguments["sku"]) for c in result.tool_calls] == [
            ("a", "PHN-001"),
            ("b", "LAP-002"),
        ]

    async def test_truncated_arguments_are_flagged_not_guessed(self):
        conversation, _ = conversation_for(
            [
                chunk(tool_fragment(0, id="a", name="get_order_status", args='{"order_code": "DH20')),
                chunk(finish="length"),
            ]
        )
        _, result = await run(conversation)
        assert result.tool_calls[0].malformed is True
        assert result.tool_calls[0].arguments == {}

    async def test_content_filter_counts_as_a_refusal(self):
        conversation, _ = conversation_for([chunk(finish="content_filter")])
        _, result = await run(conversation)
        assert result.refused is True
        assert result.refusal_detail == "content_filter"

    async def test_explicit_refusal_text_is_reported(self):
        conversation, _ = conversation_for(
            [chunk({"refusal": "I can't help with that."}), chunk(finish="stop")]
        )
        _, result = await run(conversation)
        assert result.refused is True
        assert result.refusal_detail == "I can't help with that."

    async def test_usage_arrives_in_the_final_chunk(self):
        conversation, completions = conversation_for(
            [
                chunk({"content": "OK"}),
                chunk(finish="stop"),
                chunk(
                    usage={
                        "prompt_tokens": 1200,
                        "completion_tokens": 80,
                        "total_tokens": 1280,
                        "prompt_tokens_details": {"cached_tokens": 1024},
                    }
                ),
            ]
        )
        _, result = await run(conversation)
        assert (result.usage.input_tokens, result.usage.cache_read_tokens) == (176, 1024)
        assert result.usage.output_tokens == 80
        assert completions.requests[0]["stream_options"] == {"include_usage": True}

    async def test_the_stream_is_closed_after_reading(self):
        conversation, completions = conversation_for([chunk({"content": "OK"}), chunk(finish="stop")])
        await run(conversation)
        assert completions.stream.closed is True


class TestOpenAIRequest:
    async def test_system_prompt_leads_the_messages(self):
        conversation, completions = conversation_for([chunk(finish="stop")])
        await run(conversation)
        sent = completions.requests[0]["messages"]
        assert sent[0] == {"role": "system", "content": "SYSTEM"}
        assert sent[1] == {"role": "user", "content": "hi"}

    async def test_effort_is_sent_as_reasoning_effort(self):
        conversation, completions = conversation_for([chunk(finish="stop")], effort="medium")
        await run(conversation)
        assert completions.requests[0]["reasoning_effort"] == "medium"

    async def test_no_effort_sends_no_parameter(self):
        conversation, completions = conversation_for([chunk(finish="stop")])
        await run(conversation)
        assert "reasoning_effort" not in completions.requests[0]

    async def test_tool_results_follow_the_assistant_calls_they_answer(self):
        """OpenAI rejects a tool message whose call is not in the preceding assistant message."""
        conversation, completions = conversation_for(
            [
                chunk(tool_fragment(0, id="call_1", name="ping", args='{"word": "hi"}')),
                chunk(finish="tool_calls"),
            ],
            tools=[{"name": "ping", "description": "d", "input_schema": {"type": "object"}}],
        )
        _, result = await run(conversation)
        conversation.add_tool_results(
            [ToolOutcome(call=result.tool_calls[0], content='{"echo": "hi"}', is_error=False)]
        )
        await run(conversation)

        second = completions.requests[1]["messages"]
        assistant, tool = second[-2], second[-1]
        assert assistant["role"] == "assistant"
        assert assistant["tool_calls"][0]["id"] == "call_1"
        # The original argument string, byte for byte, not a re-serialisation.
        assert assistant["tool_calls"][0]["function"]["arguments"] == '{"word": "hi"}'
        assert tool == {"role": "tool", "tool_call_id": "call_1", "content": '{"echo": "hi"}'}


# --- Tool definition conversion ---------------------------------------------


class TestOpenAITools:
    def test_registry_definitions_convert_to_function_tools(self):
        converted = to_openai_tools(registry.definitions())
        assert {tool["type"] for tool in converted} == {"function"}
        assert [t["function"]["name"] for t in converted] == registry.names()
        for original, tool in zip(registry.definitions(), converted):
            assert tool["function"]["parameters"] == original["input_schema"]

    def test_strict_is_sent_only_where_openai_accepts_it(self):
        """Optional filters make most tool schemas ineligible; sending strict anyway is a 400."""
        converted = {t["function"]["name"]: t["function"] for t in to_openai_tools(registry.definitions())}
        for original in registry.definitions():
            eligible = openai_strict_compatible(original["input_schema"])
            assert ("strict" in converted[original["name"]]) == eligible

    def test_strict_compatibility_rules(self):
        closed = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
        assert openai_strict_compatible(closed)
        assert not openai_strict_compatible({**closed, "required": []})
        assert not openai_strict_compatible({**closed, "additionalProperties": True})
        nested = {**closed, "properties": {"a": {"type": "object", "properties": {"b": {"type": "string"}}}}}
        assert not openai_strict_compatible(nested)

    def test_no_strict_when_switched_off(self):
        converted = to_openai_tools(registry.definitions(strict=False))
        assert all("strict" not in tool["function"] for tool in converted)


def test_usage_without_a_usage_block_is_zero():
    assert openai_usage(None).input_tokens == 0


# --- Anthropic conversation ---------------------------------------------------


class TestAnthropicConversation:
    def test_tool_results_go_back_in_one_user_message(self):
        conversation = AnthropicConversation(
            client=None,  # type: ignore[arg-type]
            model="m",
            system="S",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            effort=None,
        )
        conversation._last_content = ["<assistant blocks>"]
        calls = [ToolCall(id="t1", name="a", arguments={}), ToolCall(id="t2", name="b", arguments={})]
        conversation.add_tool_results(
            [ToolOutcome(call=c, content="{}", is_error=i == 1) for i, c in enumerate(calls)]
        )
        assistant, results = conversation._messages[-2], conversation._messages[-1]
        assert assistant == {"role": "assistant", "content": ["<assistant blocks>"]}
        assert results["role"] == "user"
        assert [b["tool_use_id"] for b in results["content"]] == ["t1", "t2"]
        assert [b["is_error"] for b in results["content"]] == [False, True]

    def test_system_prompt_carries_the_cache_breakpoint(self):
        conversation = AnthropicConversation(
            client=None, model="m", system="S", messages=[], tools=[], effort=None  # type: ignore[arg-type]
        )
        assert conversation._system == [
            {"type": "text", "text": "S", "cache_control": {"type": "ephemeral"}}
        ]

    def test_minimal_effort_is_not_sent_to_anthropic(self):
        """Anthropic has no 'minimal'; its classifier model rejects effort entirely."""
        conversation = AnthropicConversation(
            client=None, model="m", system="S", messages=[], tools=[], effort="minimal"  # type: ignore[arg-type]
        )
        assert conversation._effort == {}


# --- Provider selection and settings ------------------------------------------


class TestProviderSelection:
    def test_azure_openai_selects_the_openai_backend(self):
        assert isinstance(build_backend(azure()), OpenAIBackend)

    @pytest.mark.parametrize("provider", ["anthropic", "cortex"])
    def test_anthropic_format_providers_select_the_anthropic_backend(self, provider, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        extra = (
            {"snowflake_account_url": "https://a.snowflakecomputing.com", "snowflake_pat": "p"}
            if provider == "cortex"
            else {}
        )
        assert isinstance(build_backend(settings(llm_provider=provider, **extra)), AnthropicBackend)

    def test_azure_openai_without_credentials_fails_at_startup(self):
        with pytest.raises(ValidationError, match="AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY"):
            settings(llm_provider="azure_openai")

    def test_azure_base_url_is_the_v1_api(self):
        assert azure_openai_base_url(ENDPOINT) == f"{ENDPOINT}/openai/v1/"
        assert azure_openai_base_url(ENDPOINT + "/") == f"{ENDPOINT}/openai/v1/"

    def test_azure_key_is_masked_in_reprs(self):
        assert "key" not in repr(azure().azure_openai_api_key)
