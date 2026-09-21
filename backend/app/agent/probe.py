"""Check that the configured model provider supports everything the app uses.

    python -m app.agent.probe

Retrieval failures and model failures look identical from the chat window, and
so do provider incompatibilities: a rejected parameter or a misread stream
surfaces as a generic error on the first real turn. This exercises each
capability the app relies on, through the same provider-neutral interface the
app itself uses (app/agent/llm.py), and reports each separately.

The tool round trip is the check that matters most. It makes the model call a
tool, sends the result back, and requires an answer afterwards, which covers the
part of each wire format that differs most between providers: how tool calls
arrive (whole blocks, or JSON fragments to reassemble) and how results must be
returned.

About ten small requests. Costs a fraction of a cent. Needs no database.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable

from app.agent.llm import Effort, RoundResult, ToolOutcome, build_backend
from app.config import Settings, get_settings

settings = get_settings()

PING_TOOL = {
    "name": "ping",
    "description": "Echo a word back. Call this whenever you are asked to ping.",
    "input_schema": {
        "type": "object",
        "properties": {"word": {"type": "string"}},
        "required": ["word"],
        "additionalProperties": False,
    },
}


def backend_with(**overrides):
    """A backend for one check, with the optional features set explicitly.

    Required checks run with both optional features off, so a provider that
    rejects one of them still gets a clean verdict on everything else.
    """
    configured: Settings = settings.model_copy(
        update={"llm_effort": False, "llm_strict_tools": False, **overrides}
    )
    return build_backend(configured)


async def run_round(conversation) -> tuple[RoundResult, int]:
    deltas = 0
    result = None
    async for item in conversation.stream_round():
        if isinstance(item, RoundResult):
            result = item
        else:
            deltas += 1
    assert result is not None
    return result, deltas


def say_ok(backend, effort: Effort | None = None):
    return backend.conversation(
        system="You are a terse test harness.",
        messages=[{"role": "user", "content": "Reply with the single word OK."}],
        tools=[],
        effort=effort,
    )


async def text_round() -> str:
    result, deltas = await run_round(say_ok(backend_with()))
    if not result.text.strip():
        raise AssertionError("empty reply")
    if deltas == 0:
        raise AssertionError("reply arrived without any streamed text deltas")
    return f"{deltas} streamed deltas, reply={result.text.strip()[:20]!r}"


async def tool_round_trip() -> str:
    conversation = backend_with().conversation(
        system="You are a test harness. Use tools when asked.",
        messages=[{"role": "user", "content": "Ping with the word hello, then tell me what came back."}],
        tools=[PING_TOOL],
        effort=None,
    )
    first, _ = await run_round(conversation)
    calls = [call for call in first.tool_calls if call.name == "ping"]
    if not calls:
        raise AssertionError(f"no ping call; the model replied {first.text[:60]!r}")
    if calls[0].malformed or calls[0].arguments.get("word") is None:
        raise AssertionError(f"arguments did not parse: {calls[0].arguments}")
    conversation.add_tool_results(
        [ToolOutcome(call=call, content='{"echo": "hello"}', is_error=False) for call in first.tool_calls]
    )
    second, _ = await run_round(conversation)
    if second.tool_calls and not second.text.strip():
        raise AssertionError("model kept calling tools instead of answering")
    if not second.text.strip():
        raise AssertionError("no answer after the tool result")
    return f"called ping({calls[0].arguments}), then answered"


async def classifier_schema() -> str:
    from app.agent.classifier import CLASSIFIER_PROMPT, TurnClassification

    parsed = await backend_with().parse(
        model=settings.classifier_model,
        system=CLASSIFIER_PROMPT,
        user="Latest message:\nĐơn hàng DH2026090001 của mình tới đâu rồi?",
        schema=TurnClassification,
        max_tokens=256,
        effort="minimal",
    )
    if parsed is None:
        raise AssertionError("the model declined")
    return f"intent={parsed.intent}, language={parsed.language}"


async def judge_schema() -> str:
    from evals.judges import JUDGE_SYSTEM, Judgement

    parsed = await backend_with().parse(
        model=settings.agent_model,
        system=JUDGE_SYSTEM,
        user="Shopper message:\nHi\n\nRubric for a correct reply:\nGreets back.\n\nReply to grade:\nHello!",
        schema=Judgement,
        max_tokens=1000,
        effort="low",
    )
    if parsed is None:
        raise AssertionError("the model declined")
    return f"score={parsed.score}"


async def effort() -> str:
    result, _ = await run_round(say_ok(backend_with(llm_effort=True), effort="low"))
    return f"reply={result.text.strip()[:20]!r}"


async def strict_tools() -> str:
    conversation = backend_with(llm_strict_tools=True).conversation(
        system="You are a test harness. Use tools when asked.",
        messages=[{"role": "user", "content": "Ping with the word hello."}],
        tools=[{**PING_TOOL, "strict": True}],
        effort=None,
    )
    result, _ = await run_round(conversation)
    return f"{len(result.tool_calls)} tool call(s)"


STRICT_LABEL = "strict tool schemas  -> LLM_STRICT_TOOLS"
EFFORT_LABEL = "reasoning effort     -> LLM_EFFORT"

CHECKS: list[tuple[str, Callable[[], Awaitable[str]], bool]] = [
    # (label, check, required). A required failure means the app cannot run on
    # this provider; an optional one means switching the matching setting off.
    ("streamed text round (agent model)", text_round, True),
    ("tool call round trip", tool_round_trip, True),
    ("structured output: classifier", classifier_schema, True),
    ("structured output: judge", judge_schema, True),
    (STRICT_LABEL, strict_tools, False),
    (EFFORT_LABEL, effort, False),
]


def describe(error: Exception) -> str:
    cause = error.__cause__ or error
    return f"{type(cause).__name__}: {str(cause)[:170]}"


async def main() -> int:
    print(f"Provider: {settings.llm_provider}")
    if settings.llm_provider == "cortex":
        print(f"Endpoint: {settings.snowflake_account_url}")
    elif settings.llm_provider == "azure_openai":
        print(f"Endpoint: {settings.azure_openai_endpoint}")
    # Credentials are never printed.
    print(f"Models:   agent={settings.agent_model}  classifier={settings.classifier_model}\n")

    results: dict[str, bool] = {}
    required_failed = False
    for label, check, required in CHECKS:
        try:
            detail = await check()
            results[label] = True
            print(f"  PASS  {label:<42} {detail}")
        except Exception as error:  # noqa: BLE001 - every failure is a finding here
            results[label] = False
            required_failed |= required
            print(f"  FAIL  {label:<42} {describe(error)}")

    if not results["streamed text round (agent model)"]:
        # Nothing basic got through, so the other failures share one cause and
        # say nothing about the provider's features.
        print("\nThe provider could not be used. Check the credentials and model names in backend/.env.")
        return 1

    print("\nSet in backend/.env:")
    print(f"  LLM_STRICT_TOOLS={'true' if results[STRICT_LABEL] else 'false'}")
    print(f"  LLM_EFFORT={'true' if results[EFFORT_LABEL] else 'false'}")

    if required_failed:
        print("\nA required check failed. The app will not work on this provider as configured.")
        return 1
    print("\nAll required checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
