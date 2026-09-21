"""Index definitions and a bootstrap routine.

Two kinds of index are created here:

*Regular* indexes (unique keys, sort keys) work on any MongoDB deployment.

*Atlas Search* indexes (`vectorSearch` and `search` types) only exist on Atlas or
on a self-managed deployment running the search process. Creating them is
idempotent and asynchronous: the call returns immediately and Atlas builds the
index in the background, so the seed script polls until they are queryable.

Embedding mode
--------------
`settings.embedding_mode` picks how vectors are produced.

- "auto"     Atlas Automated Embedding. The index declares an `autoEmbed` field
             pointing at `embedding_source`, and Atlas calls Voyage AI itself on
             insert, on update, and at query time. Nothing in this codebase ever
             computes a vector. This is the default.
- "explicit" We compute vectors ourselves and store them in an `embedding`
             field. Used as a fallback, because Automated Embedding is in public
             preview. The query code branches in app/db/vector.py; nothing else
             in the application changes.
"""

from __future__ import annotations

import asyncio

from pymongo.operations import SearchIndexModel

from app.config import get_settings
from app.db import schema
from app.db.client import get_db


def _vector_field(path: str = "embedding_source") -> dict:
    """The vector half of a vectorSearch index, in whichever mode is configured."""
    settings = get_settings()
    if settings.embedding_mode == "auto":
        return {
            "type": "autoEmbed",
            "path": path,
            "model": settings.voyage_model,
        }
    return {
        "type": "vector",
        "path": "embedding",
        "numDimensions": settings.embedding_dimensions,
        "similarity": "cosine",
        # Scalar quantization cuts index memory roughly 4x at a small recall
        # cost. It matters on the free M0 tier, which is memory constrained.
        "quantization": "scalar",
    }


def products_vector_definition() -> dict:
    return {
        "fields": [
            _vector_field(),
            # Filter fields let $vectorSearch narrow candidates *before* the
            # vector comparison, which is what makes "jackets under 500k with
            # 4+ stars" both correct and fast.
            {"type": "filter", "path": "category"},
            {"type": "filter", "path": "subcategory"},
            {"type": "filter", "path": "brand"},
            {"type": "filter", "path": "price"},
            {"type": "filter", "path": "rating"},
            {"type": "filter", "path": "stock"},
        ]
    }


def policies_vector_definition() -> dict:
    return {
        "fields": [
            _vector_field(),
            {"type": "filter", "path": "policy_type"},
        ]
    }


def products_text_definition() -> dict:
    """Keyword index, the other half of hybrid search.

    Vector search alone is weak on exact tokens: a shopper searching for a model
    number or an exact brand wants a literal match, not a semantic neighbour.
    `dynamic: false` keeps the index small by listing only the fields worth
    matching on.
    """
    return {
        "mappings": {
            "dynamic": False,
            "fields": {
                "name_vi": {"type": "string"},
                "name_en": {"type": "string"},
                "brand": {"type": "string"},
                "category": {"type": "string"},
                "subcategory": {"type": "string"},
                "tags": {"type": "string"},
                "description_vi": {"type": "string"},
                "description_en": {"type": "string"},
            },
        }
    }


async def ensure_regular_indexes() -> list[str]:
    """Create the plain indexes. Idempotent."""
    db = get_db()
    created: list[str] = []

    await db[schema.PRODUCTS].create_index("sku", unique=True)
    await db[schema.PRODUCTS].create_index([("category", 1), ("price", 1)])
    created += ["products.sku", "products.category_price"]

    await db[schema.POLICIES].create_index("chunk_id", unique=True)
    await db[schema.POLICIES].create_index("policy_type")
    created += ["policies.chunk_id", "policies.policy_type"]

    await db[schema.ORDERS].create_index("order_code", unique=True)
    # Sparse: website orders have no marketplace code, and a plain unique index
    # would treat all of their missing values as one colliding null.
    await db[schema.ORDERS].create_index(
        "channel_order_code", unique=True, sparse=True
    )
    await db[schema.ORDERS].create_index("channel")
    created += ["orders.order_code", "orders.channel_order_code", "orders.channel"]

    await db[schema.CONVERSATIONS].create_index("session_id", unique=True)
    await db[schema.CONVERSATIONS].create_index("updated_at")
    created += ["conversations.session_id", "conversations.updated_at"]

    # The dashboard reads events by time window and groups by intent.
    await db[schema.EVENTS].create_index([("at", -1)])
    await db[schema.EVENTS].create_index([("intent", 1), ("at", -1)])
    created += ["events.at", "events.intent_at"]

    await db[schema.HANDOFFS].create_index("ticket_id", unique=True)
    await db[schema.HANDOFFS].create_index([("status", 1), ("created_at", -1)])
    created += ["handoffs.ticket_id", "handoffs.status_created"]

    return created


async def ensure_search_indexes() -> list[str]:
    """Create the Atlas Search indexes, skipping any that already exist."""
    settings = get_settings()
    db = get_db()

    wanted = [
        (schema.PRODUCTS, settings.products_vector_index, products_vector_definition(), "vectorSearch"),
        (schema.PRODUCTS, settings.products_text_index, products_text_definition(), "search"),
        (schema.POLICIES, settings.policies_vector_index, policies_vector_definition(), "vectorSearch"),
    ]

    created: list[str] = []
    for collection, name, definition, index_type in wanted:
        existing = {
            idx["name"] async for idx in await db[collection].list_search_indexes()
        }
        if name in existing:
            continue
        await db[collection].create_search_index(
            SearchIndexModel(definition=definition, name=name, type=index_type)
        )
        created.append(f"{collection}.{name}")

    return created


async def wait_until_queryable(timeout_seconds: int = 600, poll_seconds: int = 5) -> bool:
    """Block until every search index reports queryable, or the timeout expires.

    Atlas builds search indexes in the background. Querying one before it is
    ready returns no results rather than an error, which is a confusing way to
    discover the problem, so the seed script waits here instead.
    """
    settings = get_settings()
    db = get_db()
    targets = [
        (schema.PRODUCTS, settings.products_vector_index),
        (schema.PRODUCTS, settings.products_text_index),
        (schema.POLICIES, settings.policies_vector_index),
    ]

    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        ready = True
        for collection, name in targets:
            status = None
            async for idx in await db[collection].list_search_indexes(name):
                status = idx.get("queryable", False)
            if not status:
                ready = False
                break
        if ready:
            return True
        await asyncio.sleep(poll_seconds)

    return False
