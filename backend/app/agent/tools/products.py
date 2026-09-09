"""Product consultation tools: search, detail lookup, and comparison."""

from __future__ import annotations

from typing import Any

from app.agent.tools.base import ToolContext, ToolResult, tool
from app.config import get_settings
from app.db import schema
from app.db.client import get_db
from app.db.vector import rrf_merge, text_stage, vector_stage

# Slugs the model may filter on, exposed as an enum in the tool schema so it
# cannot invent a category that silently matches nothing. Defined in db.schema
# so the catalogue and the tool cannot drift apart.
CATEGORIES = schema.PRODUCT_CATEGORIES

# Fields returned to the model. Deliberately excludes `embedding_source`, which
# is long, duplicated across languages, and useless to the model: including it
# would roughly triple the token cost of every search result.
_CARD_FIELDS = {
    "_id": 0,
    "sku": 1,
    "name_vi": 1,
    "name_en": 1,
    "brand": 1,
    "category": 1,
    "category_vi": 1,
    "category_en": 1,
    "price": 1,
    "sale_price": 1,
    "rating": 1,
    "review_count": 1,
    "stock": 1,
    "images": 1,
}


def _localise(document: dict, lang: str) -> dict:
    """Collapse the bilingual fields down to the language of the conversation.

    Sending both languages would double the tokens the model reads for no gain:
    it only ever answers in one of them.
    """
    suffix = "vi" if lang == "vi" else "en"
    card = {
        "sku": document["sku"],
        "name": document.get(f"name_{suffix}"),
        "brand": document.get("brand"),
        "category": document.get("category"),
        "category_label": document.get(f"category_{suffix}"),
        "price": document.get("price"),
        "sale_price": document.get("sale_price"),
        "currency": "VND",
        "rating": document.get("rating"),
        "review_count": document.get("review_count"),
        "in_stock": bool(document.get("stock", 0) > 0),
        "stock": document.get("stock"),
        "image": (document.get("images") or [None])[0],
        "source_id": f"product:{document['sku']}",
    }
    if description := document.get(f"description_{suffix}"):
        card["description"] = description
    if attributes := document.get("attributes"):
        card["specifications"] = {
            attribute["key"]: {
                "label": attribute[f"label_{suffix}"],
                "value": attribute[f"value_{suffix}"],
            }
            for attribute in attributes
        }
    return card


def _build_filters(
    category: str | None,
    min_price: int | None,
    max_price: int | None,
    min_rating: float | None,
    in_stock_only: bool,
) -> dict[str, Any]:
    """Translate tool arguments into an Atlas `$vectorSearch` filter.

    Price filtering uses `price`, the list price, because that is the indexed
    filter field. Discounted items are re-checked after retrieval so a product
    on sale below the ceiling is not wrongly dropped.
    """
    filters: dict[str, Any] = {}
    if category:
        filters["category"] = category
    if min_price is not None or max_price is not None:
        price_filter: dict[str, int] = {}
        if min_price is not None:
            price_filter["$gte"] = min_price
        if max_price is not None:
            price_filter["$lte"] = max_price
        filters["price"] = price_filter
    if min_rating is not None:
        filters["rating"] = {"$gte": min_rating}
    if in_stock_only:
        filters["stock"] = {"$gt": 0}
    return filters


@tool(
    name="search_products",
    description=(
        "Search the product catalogue by meaning and by keyword, with optional "
        "filters on category, price, and rating. Use this whenever the shopper "
        "describes what they want rather than naming an exact product. The query "
        "may be written in Vietnamese or English; both search the same catalogue. "
        "Returns product cards with price, rating, and stock. Prices are in "
        "Vietnamese dong."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What the shopper is looking for, in their own words, for "
                    "example 'ao khoac nam giu am mua dong' or "
                    "'noise cancelling headphones for studying'."
                ),
            },
            "category": {
                "type": ["string", "null"],
                "enum": [*CATEGORIES, None],
                "description": "Restrict to one category. Null searches all categories.",
            },
            "min_price": {
                "type": ["integer", "null"],
                "description": "Minimum price in Vietnamese dong, or null.",
            },
            "max_price": {
                "type": ["integer", "null"],
                "description": "Maximum price in Vietnamese dong, or null.",
            },
            "min_rating": {
                "type": ["number", "null"],
                "description": "Minimum average rating from 0 to 5, or null.",
            },
            "in_stock_only": {
                "type": "boolean",
                "description": "When true, exclude products that are out of stock.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "How many products to return. Prefer 3 to 5 when advising.",
            },
        },
        "required": [
            "query",
            "category",
            "min_price",
            "max_price",
            "min_rating",
            "in_stock_only",
            "limit",
        ],
        "additionalProperties": False,
    },
)
async def search_products(
    *,
    context: ToolContext,
    query: str,
    category: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    min_rating: float | None = None,
    in_stock_only: bool = False,
    limit: int = 5,
) -> ToolResult:
    settings = get_settings()
    collection = get_db()[schema.PRODUCTS]
    filters = _build_filters(category, min_price, max_price, min_rating, in_stock_only)

    # Over-fetch from each retriever so fusion has room to reorder. Fusing two
    # lists of exactly `limit` items would mostly reproduce the vector ranking.
    fetch = min(max(limit * 4, 20), 50)

    semantic_stage = await vector_stage(
        index=settings.products_vector_index,
        query_text=query,
        limit=fetch,
        filters=filters or None,
    )
    semantic = await (
        await collection.aggregate([semantic_stage, {"$project": _CARD_FIELDS}])
    ).to_list(fetch)

    # Keyword half. `$search` filters differently from `$vectorSearch`, so the
    # same constraints are applied afterwards with a plain `$match`.
    keyword_pipeline: list[dict] = text_stage(
        index=settings.products_text_index,
        query_text=query,
        paths=["name_vi", "name_en", "brand", "tags", "description_vi", "description_en"],
        limit=fetch,
    )
    if filters:
        keyword_pipeline.append({"$match": filters})
    keyword_pipeline.append({"$project": _CARD_FIELDS})
    keyword = await (await collection.aggregate(keyword_pipeline)).to_list(fetch)

    # Semantic results are weighted higher: shoppers describe needs ("something
    # warm for winter") far more often than they type exact model names.
    merged = rrf_merge([semantic, keyword], key="sku", weights=[1.0, 0.6], limit=limit)

    cards = [_localise(document, context.lang) for document in merged]

    return ToolResult(
        data={
            "query": query,
            "filters_applied": filters or None,
            "result_count": len(cards),
            "products": cards,
        },
        sources=[card["source_id"] for card in cards],
        products=cards,
    )


@tool(
    name="get_product_details",
    description=(
        "Fetch the full specification sheet, description, price, and stock for "
        "one product identified by its SKU. Use this after search_products when "
        "the shopper asks about a specific item."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sku": {"type": "string", "description": "Product SKU, for example 'PHN-001'."},
        },
        "required": ["sku"],
        "additionalProperties": False,
    },
)
async def get_product_details(*, context: ToolContext, sku: str) -> ToolResult:
    document = await get_db()[schema.PRODUCTS].find_one({"sku": sku}, {"_id": 0})
    if document is None:
        return ToolResult(
            data={"error": f"No product exists with SKU {sku!r}."},
            is_error=True,
        )

    card = _localise(document, context.lang)
    return ToolResult(data={"product": card}, sources=[card["source_id"]], products=[card])


@tool(
    name="compare_products",
    description=(
        "Compare two or three products side by side on price, rating, stock, and "
        "specifications. Use this when the shopper is choosing between options "
        "they have already seen."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "skus": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 3,
                "description": "SKUs to compare.",
            },
        },
        "required": ["skus"],
        "additionalProperties": False,
    },
)
async def compare_products(*, context: ToolContext, skus: list[str]) -> ToolResult:
    documents = await get_db()[schema.PRODUCTS].find({"sku": {"$in": skus}}, {"_id": 0}).to_list(3)
    found = {document["sku"]: document for document in documents}

    missing = [sku for sku in skus if sku not in found]
    if missing:
        return ToolResult(
            data={"error": f"No product exists with SKU {', '.join(repr(s) for s in missing)}."},
            is_error=True,
        )

    # Preserve the order the model asked for; $in returns natural order.
    cards = [_localise(found[sku], context.lang) for sku in skus]

    # Only specifications every product shares can be compared row by row.
    shared_keys = set.intersection(
        *(set(card.get("specifications", {})) for card in cards)
    ) if cards else set()

    comparison = {
        key: {card["sku"]: card["specifications"][key]["value"] for card in cards}
        for key in sorted(shared_keys)
    }

    return ToolResult(
        data={
            "products": cards,
            "comparable_specifications": comparison,
            "note": (
                "Only specifications present on every product are compared. "
                "Products from different categories may share none."
            ),
        },
        sources=[card["source_id"] for card in cards],
        products=cards,
    )
