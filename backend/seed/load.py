"""Load the generated catalogue, policies, and orders into MongoDB.

Usage::

    python -m seed.load                 # replace everything, then wait for indexes
    python -m seed.load --no-wait       # skip the index readiness wait
    python -m seed.load --drop-only     # empty the collections and stop

The script is safe to re-run. Generated data is deterministic, so a re-run
reproduces identical documents and any citation or SKU referenced by the
evaluation dataset stays valid.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import get_settings
from app.db import schema
from app.db.client import close_client, get_db
from app.db.indexes import ensure_regular_indexes, ensure_search_indexes, wait_until_queryable
from app.db.vector import embed_documents
from seed.catalog import generate_products
from seed.orders import generate_orders
from seed.parse_policies import parse_all

# Collections holding generated content. Conversations, events, and handoffs are
# runtime data and are deliberately not touched by a re-seed.
SEEDED_COLLECTIONS = [schema.PRODUCTS, schema.POLICIES, schema.ORDERS]


async def drop_seeded() -> None:
    db = get_db()
    for name in SEEDED_COLLECTIONS:
        await db[name].delete_many({})
        print(f"  cleared {name}")


async def load_products() -> int:
    products = generate_products()
    documents = [product.model_dump() for product in products]

    if get_settings().embedding_mode == "explicit":
        print("  computing product embeddings via Voyage ...")
        vectors = await embed_documents([d["embedding_source"] for d in documents])
        for document, vector in zip(documents, vectors, strict=True):
            document["embedding"] = vector

    await get_db()[schema.PRODUCTS].insert_many(documents)
    return len(documents)


async def load_policies() -> int:
    chunks = parse_all()
    documents = [chunk.model_dump() for chunk in chunks]

    if get_settings().embedding_mode == "explicit":
        print("  computing policy embeddings via Voyage ...")
        vectors = await embed_documents([d["embedding_source"] for d in documents])
        for document, vector in zip(documents, vectors, strict=True):
            document["embedding"] = vector

    await get_db()[schema.POLICIES].insert_many(documents)
    return len(documents)


async def load_orders() -> int:
    orders = generate_orders()
    await get_db()[schema.ORDERS].insert_many([order.model_dump() for order in orders])
    return len(orders)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the chatbot database")
    parser.add_argument("--no-wait", action="store_true",
                        help="do not wait for Atlas Search indexes to become queryable")
    parser.add_argument("--drop-only", action="store_true",
                        help="empty the seeded collections and exit")
    parser.add_argument("--skip-indexes", action="store_true",
                        help="do not create indexes (useful against a plain MongoDB)")
    args = parser.parse_args(argv)

    settings = get_settings()
    print(f"Database : {settings.mongodb_db}")
    print(f"Embedding: {settings.embedding_mode} ({settings.voyage_model})\n")

    print("Clearing seeded collections")
    await drop_seeded()

    if args.drop_only:
        print("\nDone (drop only).")
        return 0

    print("\nLoading data")
    product_count = await load_products()
    print(f"  products {product_count}")
    policy_count = await load_policies()
    print(f"  policies {policy_count}")
    order_count = await load_orders()
    print(f"  orders   {order_count}")

    if args.skip_indexes:
        print("\nSkipping index creation.")
        return 0

    print("\nCreating regular indexes")
    for name in await ensure_regular_indexes():
        print(f"  {name}")

    print("\nCreating Atlas Search indexes")
    created = await ensure_search_indexes()
    if created:
        for name in created:
            print(f"  {name}")
    else:
        print("  (all already present)")

    if not args.no_wait:
        print("\nWaiting for search indexes to become queryable ...")
        # Atlas builds these in the background. Querying too early returns zero
        # results rather than an error, which is a confusing failure to debug.
        if await wait_until_queryable():
            print("  ready")
        else:
            print("  timed out; check index status in the Atlas UI")
            return 1

    print("\nSeed complete.")
    return 0


async def run() -> int:
    """Run and close the client in the same event loop.

    The async client is bound to the loop it was created on. Closing it from a
    second asyncio.run(), as this once did, raised on every run, successful or
    not, and on a failed run buried the real error under the closing one.
    """
    try:
        return await main()
    finally:
        await close_client()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
