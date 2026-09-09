"""Exercise every tool directly, with no model in the loop.

Retrieval quality and model behaviour fail in ways that look identical from the
chat window: the assistant says it cannot find something. This script separates
the two. If search returns nothing here, the problem is the index or the seed
data, and no amount of prompt work will fix it.

It costs nothing to run in "auto" embedding mode beyond Atlas query embeddings,
and it makes no Anthropic calls at all.

    python -m seed.smoke
"""

from __future__ import annotations

import asyncio
import sys

from app.agent.tools import ToolContext
from app.agent.tools.orders import create_handoff, get_order_status
from app.agent.tools.policies import search_policies
from app.agent.tools.products import compare_products, get_product_details, search_products
from app.db.client import close_client, ping

PASS = "  ok  "
FAIL = " FAIL "


def _paid(card: dict) -> int:
    """What the shopper actually pays: the sale price when there is one."""
    sale = card.get("sale_price")
    return sale if sale is not None else card.get("price", 0)


def report(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{PASS if ok else FAIL}] {label}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")
    return ok


async def check_products(context: ToolContext) -> bool:
    ok = True

    result = await search_products(context=context, query="tai nghe chống ồn cho sinh viên", limit=3)
    hits = result.data.get("products", [])
    ok &= report(
        "search_products: Vietnamese query returns results",
        bool(hits),
        "\n".join(f"{h.get('sku')}  {h.get('name')}  {_paid(h):,}d" for h in hits[:3])
        if hits
        else "No results. The vector index is probably still building.",
    )

    english = await search_products(context=context, query="noise cancelling headphones", limit=3)
    ok &= report(
        "search_products: English query returns results",
        bool(english.data.get("products")),
        "Cross-language retrieval relies on both languages sharing one embedded field.",
    )

    # The filter is the part most likely to be wrong: a price filter that does
    # not apply is invisible until a shopper is quoted something unaffordable.
    filtered = await search_products(
        context=context, query="pin dự phòng", max_price=500_000, limit=5
    )
    results = filtered.data.get("products", [])
    # The check is on the effective price, not the list price. The index filters
    # on list price and re-checks discounted items afterwards, so a product whose
    # sale price is under the ceiling is legitimately included even though its
    # list price is above it. Asserting on list price would fail on those.
    over = [r for r in results if _paid(r) > 500_000]
    ok &= report(
        "search_products: max_price filter is enforced",
        bool(results) and not over,
        f"{len(over)} result(s) exceeded the cap: {[r.get('sku') for r in over]}"
        if over
        else f"{len(results)} results, all at or below 500,000d",
    )

    rated = await search_products(context=context, query="điện thoại", min_rating=4.0, limit=5)
    low = [r for r in rated.data.get("products", []) if r.get("rating", 0) < 4.0]
    ok &= report(
        "search_products: min_rating filter is enforced",
        not low,
        f"{len(low)} result(s) below 4.0" if low else "",
    )

    if hits:
        sku = hits[0]["sku"]
        detail = await get_product_details(context=context, sku=sku)
        ok &= report(
            f"get_product_details: {sku}",
            not detail.is_error,
            f"sources: {detail.sources}",
        )

    if len(hits) >= 2:
        comparison = await compare_products(
            context=context, skus=[hits[0]["sku"], hits[1]["sku"]]
        )
        ok &= report("compare_products: two products", not comparison.is_error)

    missing = await get_product_details(context=context, sku="NOPE-999")
    ok &= report(
        "get_product_details: unknown SKU is an error, not an invention",
        missing.is_error,
    )

    return ok


async def check_policies(context: ToolContext) -> bool:
    ok = True

    # Each probe is phrased the way a shopper would ask, sharing few words with
    # the policy text. Matching on keywords alone would fail these.
    probes = [
        ("Đổi trả trong bao lâu?", "return-policy#return-window"),
        ("Bao lâu thì được hoàn tiền qua Momo?", "return-policy#refund-timing"),
        ("How much is shipping to another province?", "shipping-policy#shipping-fees"),
        ("Tai nghe bảo hành mấy tháng?", "warranty-policy#warranty-period"),
        ("Can I pay cash on delivery?", "payment-policy#payment-methods"),
    ]

    for question, expected in probes:
        result = await search_policies(context=context, question=question, limit=3)
        retrieved = [chunk["chunk_id"] for chunk in result.data.get("passages", [])]
        hit = expected in retrieved
        rank = retrieved.index(expected) + 1 if hit else 0
        ok &= report(
            f"search_policies: {question[:44]}",
            hit,
            f"expected {expected} but got {retrieved}" if not hit else f"rank {rank}",
        )

    return ok


async def check_orders(context: ToolContext) -> bool:
    ok = True

    good = await get_order_status(
        context=context, order_code="DH2026090001", contact="0901234567"
    )
    ok &= report(
        "get_order_status: correct contact is verified",
        good.data.get("verified") is True,
        f"status: {good.data.get('status')}",
    )

    # The three checks that matter. Each one is a way to read someone else's order.
    wrong = await get_order_status(
        context=context, order_code="DH2026090001", contact="0900000000"
    )
    ok &= report(
        "get_order_status: wrong contact is refused",
        wrong.data.get("verified") is False and "status" not in wrong.data,
    )

    fragment = await get_order_status(
        context=context, order_code="DH2026090001", contact="4567"
    )
    ok &= report(
        "get_order_status: a 4-digit fragment is refused",
        fragment.data.get("verified") is False,
        "Four digits is 10,000 guesses, not a credential.",
    )

    unknown = await get_order_status(
        context=context, order_code="DH9999999999", contact="0901234567"
    )
    ok &= report(
        "get_order_status: unknown code gives the same answer as a wrong contact",
        unknown.data == wrong.data,
        "Differing responses would reveal which order codes exist.",
    )

    by_email = await get_order_status(
        context=context, order_code="DH2026090002", contact="binh.tran@example.com"
    )
    ok &= report(
        "get_order_status: email also verifies",
        by_email.data.get("verified") is True,
    )

    normalised = await get_order_status(
        context=context, order_code="DH2026090001", contact="+84 901 234 567"
    )
    ok &= report(
        "get_order_status: phone formatting is normalised",
        normalised.data.get("verified") is True,
        "+84 901 234 567 and 0901234567 are the same number.",
    )

    return ok


async def check_handoff(context: ToolContext) -> bool:
    result = await create_handoff(
        context=context, reason="smoke test", summary="Created by seed.smoke."
    )
    return report(
        "create_handoff: opens a ticket",
        not result.is_error,
        f"ticket: {result.data.get('ticket_id')}",
    )


async def main() -> int:
    try:
        await ping()
    except Exception as exc:
        print(f"Cannot reach MongoDB: {exc}")
        print("Check MONGODB_URI in .env and your Atlas Network Access list.")
        return 2

    context = ToolContext(session_id="smoke", lang="vi")
    results = []

    for title, check in [
        ("PRODUCTS", check_products),
        ("POLICIES", check_policies),
        ("ORDERS", check_orders),
        ("HANDOFF", check_handoff),
    ]:
        print(f"\n{title}")
        print("-" * 66)
        try:
            results.append(await check(context))
        except Exception as exc:
            results.append(report(f"{title.lower()} raised", False, f"{type(exc).__name__}: {exc}"))

    await close_client()

    print("\n" + "=" * 66)
    if all(results):
        print("All checks passed. Retrieval and verification work without the model.")
        return 0
    print("Some checks failed. Fix these before judging the assistant's answers.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
