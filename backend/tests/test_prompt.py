from app.agent.orchestrator import system_prompt_for
from app.agent.prompts import SYSTEM_PROMPT


def test_reply_language_is_stated_for_each_turn():
    assert system_prompt_for("en").endswith("Reply entirely in English.")
    assert system_prompt_for("vi").endswith("Reply entirely in Vietnamese.")


def test_shared_prompt_stays_a_prefix_so_it_can_be_cached():
    assert system_prompt_for("en").startswith(SYSTEM_PROMPT)
    assert system_prompt_for("vi").startswith(SYSTEM_PROMPT)
