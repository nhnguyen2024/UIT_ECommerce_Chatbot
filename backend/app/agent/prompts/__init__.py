"""System prompt loading.

The prompt is read once at import and held as a module constant. It must stay
byte-identical between requests: it sits at the front of the cached prefix, so
any change, including an injected timestamp or a reordered line, invalidates the
prompt cache for every conversation at once.
"""

from pathlib import Path

_PROMPT_PATH = Path(__file__).parent / "system_prompt.md"

SYSTEM_PROMPT: str = _PROMPT_PATH.read_text(encoding="utf-8").strip()

__all__ = ["SYSTEM_PROMPT"]
