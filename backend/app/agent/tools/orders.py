"""Order tracking and human handoff.

Security note, and the reason this module is longer than it looks like it should
be: an order code on its own must never be sufficient to read an order. Codes are
short, look sequential, and get pasted into screenshots and group chats. If the
code alone unlocked the record, anyone could enumerate codes and harvest other
customers' names, purchases, and delivery progress.

So `get_order_status` requires a second factor: a phone number or email address
that hashes to the value stored on the order. The database holds only hashes, and
the model never sees a contact detail in either direction. A failed match returns
the same response whether or not the order code exists, so the tool cannot be
used to test which codes are real.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.agent.tools.base import ToolContext, ToolResult, tool
from app.db import schema
from app.db.client import get_db
from app.geo import PLACES
from app.security import contact_matches, mask_phone

HANDOFF_REASONS = [
    "customer_requested_human",
    "policy_not_covered",
    "complaint_or_dispute",
    "identity_verification_failed",
    "technical_issue",
]


@tool(
    name="get_order_status",
    description=(
        "Look up one order's current status, item list, and delivery timeline. "
        "Works for orders placed on the website and on Shopee, Lazada, or "
        "TikTok Shop: accepts either the store's own order code or the code the "
        "marketplace issued, whichever the shopper quotes. "
        "Requires BOTH the order code AND a contact detail that matches the "
        "order: the full phone number used to place it, or its email address. "
        "Ask the shopper for the contact detail if they have not given one; never "
        "guess it and never accept a partial phone number. If verification fails, "
        "tell the shopper the details do not match and offer a human agent. Do "
        "not reveal whether the order code itself exists."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "order_code": {
                "type": "string",
                "description": (
                    "The order code exactly as the shopper wrote it. Either the "
                    "store's own code, for example 'DH2026090001', or the "
                    "marketplace's code, for example Shopee '250905K7MQ2XPL'. "
                    "Do not try to convert between the two."
                ),
            },
            "contact": {
                "type": "string",
                "description": (
                    "The full phone number or the email address on the order. "
                    "Phone numbers may be written in any common Vietnamese "
                    "format. A partial number will not verify."
                ),
            },
        },
        "required": ["order_code", "contact"],
        "additionalProperties": False,
    },
)
async def get_order_status(
    *, context: ToolContext, order_code: str, contact: str
) -> ToolResult:
    # Either code identifies the order. A shopper who bought on a marketplace
    # was never shown the internal code, so requiring it would make this tool
    # useless for the majority of orders.
    supplied_code = order_code.strip().upper()
    document = await get_db()[schema.ORDERS].find_one(
        {"$or": [{"order_code": supplied_code}, {"channel_order_code": supplied_code}]},
        {"_id": 0},
    )

    # One response for "no such order" and "wrong contact" alike. Distinguishing
    # them would turn this tool into an oracle for which order codes are valid.
    verification_failed = ToolResult(
        data={
            "verified": False,
            "message": (
                "The order code and contact detail do not match any order. Ask "
                "the shopper to re-check both. Do not state whether the order "
                "code exists. Offer to connect them to a human agent."
            ),
        }
    )

    if document is None:
        return verification_failed

    if not contact_matches(
        supplied=contact,
        phone_hash=document["phone_hash"],
        email_hash=document["email_hash"],
    ):
        return verification_failed

    suffix = "vi" if context.lang == "vi" else "en"

    timeline = [
        {
            "status": entry["status"],
            "at": entry["at"].isoformat(),
            "note": entry.get(f"note_{suffix}"),
        }
        for entry in document.get("timeline", [])
    ]

    items = [
        {
            "sku": item["sku"],
            "name": item.get(f"name_{suffix}"),
            "quantity": item["quantity"],
            "unit_price": item["unit_price"],
        }
        for item in document.get("items", [])
    ]

    estimated = document.get("estimated_delivery")

    # The route: the model gets place names, to answer "where is it now"; the
    # map gets coordinates as well. Unknown place keys are skipped rather than
    # failing the lookup, since the order itself is still worth showing.
    stops = [
        (PLACES[stop["place"]], stop.get("arrived_at"))
        for stop in document.get("route", [])
        if stop.get("place") in PLACES
    ]
    reached = [place for place, arrived in stops if arrived is not None]
    destination = PLACES.get(document.get("destination") or "")

    return ToolResult(
        data={
            "verified": True,
            "order_code": document["order_code"],
            # The shopper thinks of the order by its marketplace code and by the
            # platform they bought on. Both are returned so the answer can name
            # them, and so return and refund questions can be routed correctly.
            "channel": document.get("channel", "website"),
            "channel_label": schema.CHANNEL_LABELS.get(
                document.get("channel", "website"), "Northlight.vn"
            ),
            "channel_order_code": document.get("channel_order_code"),
            "customer_name": document["customer_name"],
            # Masked, never the real number: enough for the shopper to recognise
            # their own order, useless to anyone who guessed the code.
            "phone_masked": mask_phone(document["phone_last4"]),
            "status": document["status"],
            "items": items,
            "subtotal": document["subtotal"],
            "shipping_fee": document["shipping_fee"],
            "total": document["total"],
            "currency": "VND",
            "carrier": document.get("carrier"),
            "tracking_code": document.get("tracking_code"),
            "estimated_delivery": estimated.date().isoformat() if estimated else None,
            "timeline": timeline,
            # Province only. There is no street address to leak.
            "destination": destination.name(suffix) if destination else None,
            "current_location": reached[-1].name(suffix) if reached else None,
            "route": [
                {"place": place.name(suffix), "arrived_at": arrived.isoformat() if arrived else None}
                for place, arrived in stops
            ],
            "placed_at": document["created_at"].date().isoformat(),
            "source_id": f"order:{document['order_code']}",
        },
        sources=[f"order:{document['order_code']}"],
        tracking={
            "order_code": document["order_code"],
            "channel_label": schema.CHANNEL_LABELS.get(
                document.get("channel", "website"), "Northlight.vn"
            ),
            "status": document["status"],
            "carrier": document.get("carrier"),
            "estimated_delivery": estimated.date().isoformat() if estimated else None,
            "stops": [
                {
                    "key": place.key,
                    "name": place.name(suffix),
                    "kind": place.kind,
                    "lat": place.lat,
                    "lon": place.lon,
                    "arrived_at": arrived.isoformat() if arrived else None,
                }
                for place, arrived in stops
            ],
        }
        if stops
        else None,
    )


@tool(
    name="create_handoff",
    description=(
        "Open a support ticket and hand the conversation to a human agent. Use "
        "this when the shopper asks for a person, when a complaint or dispute "
        "needs judgement you cannot exercise, when identity verification has "
        "failed and the shopper still needs help, or when the published policies "
        "do not cover their question. Tell the shopper the ticket number "
        "afterwards. Do not use this for ordinary questions you can answer."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "enum": HANDOFF_REASONS,
                "description": "Why the conversation needs a human.",
            },
            "summary": {
                "type": "string",
                "description": (
                    "A short handover note for the agent: what the shopper wants, "
                    "what has been tried, and what is unresolved. Do not include "
                    "phone numbers or email addresses."
                ),
            },
        },
        "required": ["reason", "summary"],
        "additionalProperties": False,
    },
)
async def create_handoff(*, context: ToolContext, reason: str, summary: str) -> ToolResult:
    ticket_id = f"TK{uuid.uuid4().hex[:10].upper()}"
    now = datetime.now(timezone.utc)

    handoff = schema.Handoff(
        ticket_id=ticket_id,
        session_id=context.session_id,
        reason=reason,
        summary=summary,
        lang=context.lang,
        status="open",
        created_at=now,
    )
    await get_db()[schema.HANDOFFS].insert_one(handoff.model_dump())

    # Flag the conversation so the UI can show a handoff banner and the
    # dashboard can count escalations without re-reading every transcript.
    await get_db()[schema.CONVERSATIONS].update_one(
        {"session_id": context.session_id}, {"$set": {"escalated": True}}
    )

    return ToolResult(
        data={
            "ticket_id": ticket_id,
            "status": "open",
            "reason": reason,
            "message": (
                "A human agent has been notified. Give the shopper the ticket "
                "number and tell them support replies within 24 working hours "
                "on 1900 1234."
            ),
        },
        sources=[f"ticket:{ticket_id}"],
    )
