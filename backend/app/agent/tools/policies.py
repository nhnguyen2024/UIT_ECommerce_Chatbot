"""Policy retrieval.

This is the retrieval-augmented half of the system. The model is not trusted to
recall store policy from training: return windows, refund timings, and fee
thresholds are business facts that change, and a plausible-sounding wrong answer
about a refund is worse than no answer. Every policy statement must come from a
chunk returned here, and must be cited by `chunk_id`.
"""

from __future__ import annotations

from app.agent.tools.base import ToolContext, ToolResult, tool
from app.config import get_settings
from app.db import schema
from app.db.client import get_db

POLICY_TYPES = ["return", "warranty", "shipping", "payment", "privacy"]

_CHUNK_FIELDS = {
    "_id": 0,
    "chunk_id": 1,
    "doc_id": 1,
    "policy_type": 1,
    "title_vi": 1,
    "title_en": 1,
    "section_vi": 1,
    "section_en": 1,
    "text_vi": 1,
    "text_en": 1,
    "source_url": 1,
    "updated_at": 1,
}


@tool(
    name="search_policies",
    description=(
        "Search official store policy documents covering returns and exchanges, "
        "warranty, shipping and delivery, payment, and privacy. Use this for ANY "
        "question about store rules, timeframes, fees, eligibility, or process. "
        "Never answer a policy question from memory: quote only what this tool "
        "returns, and cite the chunk_id of every passage you rely on. The question "
        "may be in Vietnamese or English."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "The shopper's policy question, in their own words, for "
                    "example 'doi tra trong bao lau' or 'who pays return shipping'."
                ),
            },
            "policy_type": {
                "type": ["string", "null"],
                "enum": [*POLICY_TYPES, None],
                "description": (
                    "Restrict to one policy area when the question is clearly "
                    "about it. Null searches all policies, which is safer when "
                    "the question spans areas. Refunds, including how long a "
                    "refund takes for each payment method, are under 'return', "
                    "not 'payment'; 'payment' covers how to pay, instalments, "
                    "and invoices. When unsure, use null."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 6,
                "description": "How many passages to retrieve. 3 suits most questions.",
            },
        },
        "required": ["question", "policy_type", "limit"],
        "additionalProperties": False,
    },
)
async def search_policies(
    *,
    context: ToolContext,
    question: str,
    policy_type: str | None = None,
    limit: int = 3,
) -> ToolResult:
    from app.db.vector import vector_stage  # imported here to keep import order flat

    settings = get_settings()
    filters = {"policy_type": policy_type} if policy_type else None

    stage = await vector_stage(
        index=settings.policies_vector_index,
        query_text=question,
        limit=limit,
        filters=filters,
    )
    documents = await (
        await get_db()[schema.POLICIES].aggregate([stage, {"$project": _CHUNK_FIELDS}])
    ).to_list(limit)

    suffix = "vi" if context.lang == "vi" else "en"
    passages = [
        {
            "chunk_id": document["chunk_id"],
            "policy_type": document["policy_type"],
            "document_title": document.get(f"title_{suffix}"),
            "section": document.get(f"section_{suffix}"),
            "text": document.get(f"text_{suffix}"),
            "source_url": document.get("source_url"),
            "last_updated": document["updated_at"].date().isoformat()
            if document.get("updated_at")
            else None,
        }
        for document in documents
    ]

    if not passages:
        return ToolResult(
            data={
                "question": question,
                "passages": [],
                "guidance": (
                    "No policy passage matched. Tell the shopper you cannot "
                    "confirm this from the published policies and offer to "
                    "connect them to a human agent. Do not guess."
                ),
            }
        )

    return ToolResult(
        data={
            "question": question,
            "passage_count": len(passages),
            "passages": passages,
            "citation_instruction": (
                "Cite each passage you use by writing [ref:<chunk_id>] "
                "immediately after the sentence that relies on it."
            ),
        },
        sources=[passage["chunk_id"] for passage in passages],
    )
