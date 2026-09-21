"""Shared Anthropic client.

One async client per process. The SDK pools connections, so creating a client
per request would throw away those pooled connections and add a TLS handshake to
every turn.

Clients for the three providers. Anthropic and Snowflake Cortex share one client
class, because Cortex exposes an Anthropic-compatible Messages endpoint; only the
base URL and the credential differ. Azure OpenAI uses the OpenAI client.
Callers never use these directly: app/agent/llm.py wraps them behind one
provider-neutral interface.

Credentials come from settings, which read both the environment and
backend/.env. They are held as SecretStr and unwrapped only here, at the point
they are handed to the SDK, so they never appear in a repr or a traceback.
"""

from __future__ import annotations

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.config import Settings

# Generous but finite. A turn that hangs longer than this is stuck, and the
# shopper is watching a spinner; fail and let the caller report it.
TIMEOUT_SECONDS = 120.0
MAX_RETRIES = 2


def cortex_base_url(account_url: str) -> str:
    """The Cortex Messages API root. The SDK appends `/v1/messages` itself."""
    return account_url.rstrip("/") + "/api/v2/cortex"


def build_client(settings: Settings) -> AsyncAnthropic:
    if settings.llm_provider == "cortex":
        return AsyncAnthropic(
            # Cortex authenticates with a bearer token, not Anthropic's x-api-key
            # header. The SDK insists on some api_key, so it gets a placeholder
            # that Snowflake ignores; the real credential goes in Authorization.
            #
            # Snowflake's own example passes an httpx.Client for the header. That
            # breaks on anthropic 1.x, which is built on httpx2 and rejects httpx
            # objects, so the header is set through default_headers instead.
            api_key="unused-with-cortex",
            base_url=cortex_base_url(settings.snowflake_account_url or ""),
            default_headers={
                "Authorization": f"Bearer {_reveal(settings.snowflake_pat)}",
                # Optional per the docs, but naming the token type turns a
                # malformed token into a clear error instead of a guess.
                "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
            },
            timeout=TIMEOUT_SECONDS,
            max_retries=MAX_RETRIES,
        )

    # None falls back to the SDK's own ANTHROPIC_API_KEY environment lookup.
    return AsyncAnthropic(
        api_key=_reveal(settings.anthropic_api_key),
        timeout=TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )


def azure_openai_base_url(endpoint: str) -> str:
    """Azure OpenAI's v1 API, which speaks the plain OpenAI protocol.

    The v1 path takes the deployment name as `model` and needs no api-version
    query parameter, so the standard OpenAI client works against it unchanged.
    """
    return endpoint.rstrip("/") + "/openai/v1/"


def build_openai_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=azure_openai_base_url(settings.azure_openai_endpoint or ""),
        api_key=_reveal(settings.azure_openai_api_key),
        timeout=TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )


def _reveal(secret: SecretStr | None) -> str | None:
    return secret.get_secret_value() if secret else None
