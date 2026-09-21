"""Input gates and output grounding checks.

Two stages sit around the agent.

Before the agent runs, `screen_input` can short-circuit a turn. Only two cases
are handled this way: a shopper sharing payment credentials, where an improvised
reply is a real risk, and a confidently out-of-scope request, where paying for a
full agent turn buys nothing. Everything else goes to the agent, because the
system prompt handles it better than a keyword rule could.

After the agent answers, `check_citations` verifies that every citation marker
points at something a tool actually returned this turn, and strips any that do
not. `check_numeric_grounding` then looks for figures in the answer that appear
in no tool result.

The split between the two is deliberate. A fabricated citation is a defect with
no legitimate cause, so it is removed. Numeric grounding is a heuristic with
real false positives, so it records a flag for telemetry and the evaluation
harness rather than blocking a reply that is probably correct.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# [ref:return-policy#return-window], [ref:product:PHN-000], [ref:order:DH2026090001]
CITATION_PATTERN = re.compile(r"\[ref:([^\]\s]+)\]")

# Grouped figures: 1.290.000 or 1,290,000. Bare integers are ignored because
# quantities and list positions are not claims about the store's data.
#
# The tail is a negative lookahead rather than \b. A trailing \b fails when a
# price is written with an attached currency mark, as Vietnamese prices usually
# are ("1.290.000d"): the boundary between "0" and "d" does not exist, so the
# pattern backtracks and matches only "1.290", which then reads as an
# unsupported figure and produces a false grounding failure.
GROUPED_NUMBER = re.compile(r"\b\d{1,3}(?:[.,]\d{3})+(?!\d)")

# Durations and counts that carry policy meaning: "7 ngày", "12 months".
DURATION = re.compile(
    r"\b(\d{1,3})\s*(?:ngày|ngay|day|days|tháng|thang|month|months|giờ|gio|hour|hours|năm|nam|year|years)\b",
    re.IGNORECASE,
)

CREDENTIAL_WARNING = {
    "vi": (
        "Mình không bao giờ hỏi mã OTP, mã CVV, số thẻ đầy đủ hay mật khẩu ngân "
        "hàng, và bạn cũng đừng chia sẻ những thông tin này với bất kỳ ai, kể cả "
        "người tự xưng là nhân viên cửa hàng. Nếu có ai yêu cầu, bạn hãy từ chối "
        "và gọi hotline 1900 1234. Mình có thể giúp bạn tra cứu đơn hàng, tư vấn "
        "sản phẩm hoặc giải đáp chính sách."
    ),
    "en": (
        "I will never ask for an OTP, a CVV, a full card number, or a banking "
        "password, and you should not share those with anyone, including someone "
        "claiming to be store staff. If you are asked for one, refuse and call "
        "1900 1234. I can help you track an order, choose a product, or explain "
        "our policies."
    ),
}

# Names the categories, because the commonest out-of-scope question is for a
# product the store does not carry ("do you sell running shoes?"), and a reply
# that never says what the store does sell leaves the shopper guessing.
OUT_OF_SCOPE_REPLY = {
    "vi": (
        "Xin lỗi bạn, yêu cầu này nằm ngoài phạm vi mình hỗ trợ. Northlight chỉ "
        "bán đồ điện tử: điện thoại, máy tính bảng, laptop, tai nghe và loa, đồng "
        "hồ thông minh, tivi, đồ gia dụng và phụ kiện sạc. Mình có thể tư vấn sản "
        "phẩm trong các nhóm trên, giải đáp chính sách đổi trả, bảo hành, vận "
        "chuyển, thanh toán hoặc tra cứu đơn hàng cho bạn."
    ),
    "en": (
        "Sorry, that is outside what I can help with. Northlight only sells "
        "electronics: phones, tablets, laptops, headphones and speakers, "
        "smartwatches, TVs, home appliances, and charging accessories. I can help "
        "with products in those categories, our return, warranty, shipping and "
        "payment policies, or tracking an order."
    ),
}


DECLINED_REPLY = {
    "vi": (
        "Xin lỗi, mình không thể thực hiện yêu cầu này. Mình có thể tư vấn sản "
        "phẩm, giải đáp chính sách đổi trả, bảo hành, vận chuyển, thanh toán hoặc "
        "tra cứu đơn hàng cho bạn."
    ),
    "en": (
        "Sorry, I can't help with that request. I can help you choose a product, "
        "explain our return, warranty, shipping and payment policies, or check on "
        "an order."
    ),
}


@dataclass
class InputVerdict:
    """Outcome of the pre-agent screen."""

    allowed: bool
    canned_reply: str | None = None
    reason: str | None = None


@dataclass
class CitationCheck:
    text: str
    citations: list[dict] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)


@dataclass
class GroundingCheck:
    grounded: bool
    unsupported: list[str] = field(default_factory=list)


def screen_input(*, safety: str, intent: str, confidence: float, lang: str) -> InputVerdict:
    """Decide whether a turn should reach the agent at all.

    Args:
        safety: Safety label from the turn classifier.
        intent: Intent label from the turn classifier.
        confidence: Classifier confidence. A fallback classification scores 0.0,
            and must never trigger a short circuit.
        lang: Reply language.
    """
    language = "vi" if lang == "vi" else "en"

    if safety == "sensitive_credentials":
        return InputVerdict(
            allowed=False,
            canned_reply=CREDENTIAL_WARNING[language],
            reason="sensitive_credentials",
        )

    # Only short-circuit when the classifier is confident. A failed classifier
    # returns confidence 0.0, and its default label must not silently start
    # declining real questions.
    if intent == "out_of_scope" and safety == "out_of_scope" and confidence >= 0.85:
        return InputVerdict(
            allowed=False,
            canned_reply=OUT_OF_SCOPE_REPLY[language],
            reason="out_of_scope",
        )

    # An injection attempt still goes to the agent. The system prompt instructs
    # it to treat retrieved text as data, and a canned refusal here would also
    # fire on shoppers who merely ask how the assistant works.
    return InputVerdict(allowed=True)


def check_citations(text: str, available_sources: set[str]) -> CitationCheck:
    """Validate citation markers and convert them into structured citations.

    Markers that name a source no tool returned this turn are removed from the
    text. Leaving them in would render as a broken link in the UI and, worse,
    would look like evidence for a claim that has none.
    """
    citations: list[dict] = []
    invalid: list[str] = []
    seen: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        source_id = match.group(1)
        if source_id not in available_sources:
            invalid.append(source_id)
            return ""
        if source_id not in seen:
            seen.add(source_id)
            citations.append({"source_id": source_id, "kind": _citation_kind(source_id)})
        return match.group(0)

    cleaned = CITATION_PATTERN.sub(replace, text)

    # Removing a marker can leave a doubled space or a space before punctuation.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" ([.,;:!?])", r"\1", cleaned)

    return CitationCheck(text=cleaned.strip(), citations=citations, invalid=invalid)


def _citation_kind(source_id: str) -> str:
    if source_id.startswith("product:"):
        return "product"
    if source_id.startswith("order:"):
        return "order"
    if source_id.startswith("ticket:"):
        return "ticket"
    return "policy"


def collect_numbers(payload: Any) -> set[str]:
    """Gather every figure a tool result contains, normalised for comparison.

    Numbers are collected from the structure itself (an integer price field) and
    from any text inside it (a policy passage saying "7 ngày"), because the model
    may quote either.
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int):
            found.add(str(node))
        elif isinstance(node, float):
            found.add(str(int(node)) if node.is_integer() else str(node))
        elif isinstance(node, str):
            for match in GROUPED_NUMBER.finditer(node):
                found.add(_normalise_number(match.group(0)))
            for match in DURATION.finditer(node):
                found.add(match.group(1))
            # Bare integers inside retrieved text still count as support.
            for match in re.finditer(r"\b\d+\b", node):
                found.add(match.group(0))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(payload)
    return found


def _normalise_number(text: str) -> str:
    """Strip thousands separators so 1.290.000 and 1,290,000 compare equal."""
    return re.sub(r"[.,]", "", text)


def check_numeric_grounding(text: str, tool_payloads: list[Any]) -> GroundingCheck:
    """Flag figures in the answer that no tool result supports.

    This is a heuristic and it is used as a signal, not a gate. It catches the
    failure that matters most in this domain: a confidently stated price, fee, or
    deadline that the model produced rather than retrieved.
    """
    supported: set[str] = set()
    for payload in tool_payloads:
        supported |= collect_numbers(payload)

    # A hotline number and a free-shipping threshold appear in the prompt and in
    # policy text often enough that treating them as claims adds only noise.
    supported |= {"1900", "1234", "19001234"}

    unsupported: list[str] = []

    for match in GROUPED_NUMBER.finditer(text):
        if _normalise_number(match.group(0)) not in supported:
            unsupported.append(match.group(0))

    for match in DURATION.finditer(text):
        if match.group(1) not in supported:
            unsupported.append(match.group(0))

    return GroundingCheck(grounded=not unsupported, unsupported=unsupported)
