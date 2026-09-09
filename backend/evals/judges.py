"""Rubric grading with a model judge.

Metrics like retrieval recall are computed from labels. Answer quality is not
reducible to a label, so a judge model reads the rubric and the answer and
scores 1 to 5.

Two decisions worth defending in a report:

The judge sees the rubric and the answer, but not which system produced it and
not any earlier score. That keeps it from anchoring on a previous judgement.

The judge is the same model tier as the system under test. A weaker judge
mistakes fluent wrong answers for correct ones, which is the failure mode that
matters most here, since every case is about whether a specific retrieved fact
was reported accurately.
"""

from __future__ import annotations

import logging
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from app.agent.client import get_anthropic
from app.config import get_settings

logger = logging.getLogger(__name__)


class Judgement(BaseModel):
    score: Literal[1, 2, 3, 4, 5] = Field(
        description=(
            "5: fully satisfies the rubric. "
            "4: satisfies it with a minor omission. "
            "3: partially correct, or correct but padded with unsupported claims. "
            "2: mostly wrong, or answers a different question. "
            "1: contradicts the rubric, or invents facts."
        )
    )
    reason: str = Field(description="One or two sentences citing what decided the score.")


JUDGE_SYSTEM = """You grade replies from a Vietnamese e-commerce support chatbot.

You are given a shopper message, a rubric describing what a correct reply must
contain, and the reply that was produced. Score how well the reply satisfies the
rubric.

Grade only against the rubric. Do not reward extra detail the rubric did not ask
for, and do not penalise a reply for being brief if it covers what the rubric
requires.

Specific rules:

- A reply that states a figure the rubric did not specify, such as a different
  number of days or a different fee, scores 2 or lower. Inventing facts is the
  failure this system exists to prevent.
- A reply that correctly refuses, asks for verification, or escalates, where the
  rubric calls for that, scores 5. Refusing is a correct answer, not a
  non-answer.
- Citation markers of the form [ref:...] are a feature of this system. Ignore
  them when judging readability, but treat a policy claim with no marker at all
  as a minor omission.
- Judge Vietnamese replies as a Vietnamese speaker would. A reply in the wrong
  language scores 2 or lower regardless of content."""


async def judge_answer(*, message: str, rubric: str, answer: str) -> Judgement | None:
    """Score one answer. Returns None if the judge call failed."""
    if not answer.strip():
        return Judgement(score=1, reason="The system produced no answer.")

    settings = get_settings()
    prompt = (
        f"Shopper message:\n{message}\n\n"
        f"Rubric for a correct reply:\n{rubric}\n\n"
        f"Reply to grade:\n{answer}"
    )

    try:
        response = await get_anthropic().messages.parse(
            model=settings.agent_model,
            max_tokens=1000,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=Judgement,
            # Grading is a bounded comparison against a written rubric, so the
            # extra spend of a higher effort level buys nothing measurable.
            output_config={"effort": "low"},
        )
    except anthropic.APIError:
        logger.warning("judge call failed", exc_info=True)
        return None

    return response.parsed_output
