"""The storefront: catalogue browsing and guest checkout.

This exists to show the business the chatbot serves: one catalogue sold on the
company's own website and on marketplaces. It is deliberately small. There are
no accounts and no real payment; an order placed here is an ordinary website
order, and the chatbot tracks it like any other, which is the point of the demo.

Prices and stock always come from the database. The client sends SKUs and
quantities only, so a tampered request cannot change what an order costs.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError

from app.db import schema
from app.db.client import get_db
from app.geo import (
    DESTINATIONS,
    FREE_SHIPPING_FROM,
    PLACES,
    nearest_warehouse,
    plan_route,
    shipping_fee,
)
from app.security import hash_email, hash_phone, normalize_email, normalize_phone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/shop", tags=["shop"])

LIST_FIELDS = {
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
    "listed_on": 1,
}

SORTS = {
    "popular": [("review_count", -1), ("sku", 1)],
    "rating": [("rating", -1), ("review_count", -1), ("sku", 1)],
    # Sorting on the list price alone would rank a discounted item by the price
    # nobody pays; "effective_price" is computed in the pipeline below.
    "price_asc": [("effective_price", 1), ("sku", 1)],
    "price_desc": [("effective_price", -1), ("sku", 1)],
}

PHONE = re.compile(r"^0\d{9}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _card(document: dict) -> dict:
    document = dict(document)
    document["in_stock"] = document.get("stock", 0) > 0
    document["listed_on"] = document.get("listed_on") or ["website"]
    return document


@router.get("/categories")
async def categories() -> list[dict]:
    cursor = await get_db()[schema.PRODUCTS].aggregate(
        [
            {"$group": {
                "_id": "$category",
                "label_vi": {"$first": "$category_vi"},
                "label_en": {"$first": "$category_en"},
                "count": {"$sum": 1},
            }},
        ]
    )
    rows = await cursor.to_list(None)
    order = {slug: index for index, slug in enumerate(schema.PRODUCT_CATEGORIES)}
    rows.sort(key=lambda row: order.get(row["_id"], len(order)))
    return [{"slug": row.pop("_id"), **row} for row in rows]


@router.get("/products")
async def list_products(
    category: str | None = None,
    q: str | None = Query(default=None, max_length=80),
    sort: Literal["popular", "rating", "price_asc", "price_desc"] = "popular",
    page: int = Query(default=1, ge=1, le=100),
    page_size: int = Query(default=24, ge=1, le=48),
) -> dict:
    match: dict = {}
    if category:
        match["category"] = category
    if q and q.strip():
        # A plain substring match. The chatbot has the semantic search; the
        # storefront box only needs to find a product by (part of) its name.
        pattern = re.escape(q.strip())
        match["$or"] = [
            {"name_vi": {"$regex": pattern, "$options": "i"}},
            {"name_en": {"$regex": pattern, "$options": "i"}},
            {"brand": {"$regex": pattern, "$options": "i"}},
        ]

    pipeline = [
        {"$match": match},
        {"$addFields": {"effective_price": {"$ifNull": ["$sale_price", "$price"]}}},
        {"$sort": dict(SORTS[sort])},
        {"$facet": {
            "items": [{"$skip": (page - 1) * page_size}, {"$limit": page_size}, {"$project": LIST_FIELDS}],
            "total": [{"$count": "n"}],
        }},
    ]
    cursor = await get_db()[schema.PRODUCTS].aggregate(pipeline)
    result = (await cursor.to_list(1))[0]
    total = result["total"][0]["n"] if result["total"] else 0
    return {
        "items": [_card(item) for item in result["items"]],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/products/{sku}")
async def product_detail(sku: str) -> dict:
    document = await get_db()[schema.PRODUCTS].find_one(
        {"sku": sku.upper()}, {"_id": 0, "embedding_source": 0, "embedding": 0}
    )
    if document is None:
        raise HTTPException(status_code=404, detail="No such product")
    return _card(document)


@router.get("/provinces")
async def provinces() -> list[dict]:
    return [
        {"key": key, "name_vi": PLACES[key].name_vi, "name_en": PLACES[key].name_en}
        for key in DESTINATIONS
    ]


@router.get("/shipping")
async def shipping_quote(province: str, subtotal: int = Query(ge=0)) -> dict:
    """The fee checkout will charge, so the page shows the same number the server uses."""
    if province not in DESTINATIONS:
        raise HTTPException(status_code=422, detail="Unknown province")
    return {"shipping_fee": shipping_fee(province, subtotal), "free_from": FREE_SHIPPING_FROM}


class OrderLine(BaseModel):
    sku: str = Field(min_length=3, max_length=20)
    quantity: int = Field(ge=1, le=10)


class CheckoutRequest(BaseModel):
    items: list[OrderLine] = Field(min_length=1, max_length=20)
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=9, max_length=20)
    email: str = Field(min_length=5, max_length=120)
    province: str
    payment_method: Literal["cod", "bank_transfer"]

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str) -> str:
        normalized = normalize_phone(value)
        if not PHONE.match(normalized):
            raise ValueError("Enter a Vietnamese phone number, e.g. 0901234567")
        return normalized

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not EMAIL.match(normalized):
            raise ValueError("Enter a valid email address")
        return normalized

    @field_validator("province")
    @classmethod
    def _province(cls, value: str) -> str:
        if value not in DESTINATIONS:
            raise ValueError("Choose a province from the list")
        return value


async def _reserve_stock(lines: list[OrderLine]) -> list[dict]:
    """Take the items out of stock, all or nothing.

    Each decrement is conditional on enough stock remaining, so two shoppers
    buying the last unit cannot both succeed. If a later line fails, the
    earlier ones are put back.
    """
    products = get_db()[schema.PRODUCTS]
    reserved: list[tuple[str, int]] = []
    documents: list[dict] = []
    try:
        for line in lines:
            document = await products.find_one_and_update(
                {"sku": line.sku.upper(), "stock": {"$gte": line.quantity}},
                {"$inc": {"stock": -line.quantity}},
                projection={"_id": 0, "sku": 1, "name_vi": 1, "name_en": 1, "price": 1, "sale_price": 1},
            )
            if document is None:
                raise HTTPException(
                    status_code=409, detail=f"{line.sku.upper()} is out of stock or does not exist"
                )
            reserved.append((document["sku"], line.quantity))
            documents.append({**document, "quantity": line.quantity})
    except BaseException:
        for sku, quantity in reserved:
            await products.update_one({"sku": sku}, {"$inc": {"stock": quantity}})
        raise
    return documents


@router.post("/orders", status_code=201)
async def place_order(request: CheckoutRequest) -> dict:
    # Merge repeated SKUs so a line cannot dodge the per-line quantity check.
    merged: dict[str, int] = {}
    for line in request.items:
        merged[line.sku.upper()] = merged.get(line.sku.upper(), 0) + line.quantity
    if any(quantity > 10 for quantity in merged.values()):
        raise HTTPException(status_code=422, detail="At most 10 of one product per order")
    lines = [OrderLine(sku=sku, quantity=quantity) for sku, quantity in merged.items()]

    documents = await _reserve_stock(lines)
    items = [
        schema.OrderItem(
            sku=document["sku"],
            name_vi=document["name_vi"],
            name_en=document["name_en"],
            quantity=document["quantity"],
            unit_price=document["sale_price"] if document.get("sale_price") is not None else document["price"],
        )
        for document in documents
    ]
    subtotal = sum(item.unit_price * item.quantity for item in items)
    fee = shipping_fee(request.province, subtotal)
    now = datetime.now(timezone.utc)
    warehouse = nearest_warehouse(request.province)

    order = schema.Order(
        order_code="",
        channel="website",
        channel_order_code=None,
        customer_name=request.name.strip(),
        phone_hash=hash_phone(request.phone),
        phone_last4=request.phone[-4:],
        email_hash=hash_email(request.email),
        items=items,
        subtotal=subtotal,
        shipping_fee=fee,
        total=subtotal + fee,
        status="pending",
        timeline=[
            schema.OrderTimelineEntry(
                status="pending",
                at=now,
                note_vi="Đơn hàng đã được tạo và đang chờ xác nhận.",
                note_en="Order created and awaiting confirmation.",
            )
        ],
        created_at=now,
        destination=request.province,
        route=[
            schema.RouteStop(place=place, arrived_at=now if index == 0 else None)
            for index, place in enumerate(plan_route(warehouse, request.province))
        ],
    )

    # Random, not sequential: a guessable next code invites enumeration, even
    # though a code alone never unlocks an order.
    for _ in range(5):
        order.order_code = f"DH{now:%Y%m}{secrets.randbelow(10**6):06d}"
        try:
            await get_db()[schema.ORDERS].insert_one(order.model_dump())
            break
        except DuplicateKeyError:
            continue
    else:
        for document in documents:
            await get_db()[schema.PRODUCTS].update_one(
                {"sku": document["sku"]}, {"$inc": {"stock": document["quantity"]}}
            )
        raise HTTPException(status_code=503, detail="Could not allocate an order code, please retry")

    logger.info("website order %s placed, %d line(s)", order.order_code, len(items))
    return {
        "order_code": order.order_code,
        "status": order.status,
        "items": [item.model_dump() for item in items],
        "subtotal": subtotal,
        "shipping_fee": fee,
        "total": order.total,
        "payment_method": request.payment_method,
        "province": PLACES[request.province].name_vi,
        "phone_masked": f"******{order.phone_last4}",
        "placed_at": now.isoformat(),
    }
