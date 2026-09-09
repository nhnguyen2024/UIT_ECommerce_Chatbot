"""Shared Anthropic client.

One async client per process. The SDK pools connections, so creating a client
per request would throw away those pooled connections and add a TLS handshake to
every turn.

No API key is passed explicitly: the SDK resolves credentials from the
environment, which keeps the key out of application code and out of tracebacks.
"""

from __future__ import annotations

from functools import lru_cache

from anthropic import AsyncAnthropic


@lru_cache
def get_anthropic() -> AsyncAnthropic:
    return AsyncAnthropic(
        # Generous but finite. A turn that hangs longer than this is stuck, and
        # the shopper is watching a spinner; fail and let the caller report it.
        timeout=120.0,
        max_retries=2,
    )
