"""Synthetic orders.

Two groups are produced.

*Showcase orders* have fixed codes, phone numbers, and statuses. The evaluation
dataset asserts against them and the live demo uses them, so their contents must
not drift between runs. They deliberately cover the interesting cases: an order
in transit, a delivered order, a cancelled order, a returned order, and one with
a failed delivery attempt.

*Bulk orders* are generated randomly from a fixed seed to give the dashboard
something to aggregate.

Statuses and their timelines are generated together, so an order can never claim
to be "delivered" while its timeline stops at "packing".
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.db.schema import Order, OrderItem, OrderTimelineEntry
from app.security import hash_email, hash_phone, normalize_phone
from seed.catalog import generate_products

SEED = 20260909
BULK_COUNT = 320

# Fixed "now" so timelines are reproducible across runs.
NOW = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)

CARRIERS = ["Giao Hàng Nhanh", "Giao Hàng Tiết Kiệm", "Viettel Post", "J&T Express"]

FAMILY_NAMES = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Đặng", "Bùi"]
MIDDLE_NAMES = ["Văn", "Thị", "Hữu", "Minh", "Thanh", "Ngọc", "Quang", "Gia", "Khánh"]
GIVEN_NAMES = ["An", "Bình", "Chi", "Dũng", "Hà", "Hải", "Hương", "Khoa", "Lan", "Linh",
               "Mai", "Nam", "Ngân", "Phúc", "Quân", "Sơn", "Thảo", "Trang", "Tuấn", "Vy"]

# Which timeline steps precede each status, in order.
STATUS_FLOW = {
    "pending": ["pending"],
    "confirmed": ["pending", "confirmed"],
    "packing": ["pending", "confirmed", "packing"],
    "shipped": ["pending", "confirmed", "packing", "shipped"],
    "out_for_delivery": ["pending", "confirmed", "packing", "shipped", "out_for_delivery"],
    "delivered": ["pending", "confirmed", "packing", "shipped", "out_for_delivery", "delivered"],
    "cancelled": ["pending", "confirmed", "cancelled"],
    "returned": ["pending", "confirmed", "packing", "shipped", "out_for_delivery",
                 "delivered", "returned"],
}

STATUS_NOTES = {
    "pending": ("Đơn hàng đã được tạo và đang chờ xác nhận.",
                "Order created and awaiting confirmation."),
    "confirmed": ("Đơn hàng đã được xác nhận, đang chuẩn bị hàng.",
                  "Order confirmed, preparing items."),
    "packing": ("Kho đang đóng gói sản phẩm.",
                "The warehouse is packing your items."),
    "shipped": ("Đơn hàng đã bàn giao cho đơn vị vận chuyển.",
                "Order handed over to the carrier."),
    "out_for_delivery": ("Nhân viên giao hàng đang trên đường giao đến bạn.",
                         "The courier is out for delivery."),
    "delivered": ("Đơn hàng đã được giao thành công.",
                  "Order delivered successfully."),
    "cancelled": ("Đơn hàng đã bị hủy.",
                  "Order cancelled."),
    "returned": ("Đơn hàng đã được hoàn trả về kho.",
                 "Order returned to the warehouse."),
}


def _build_timeline(status: str, created_at: datetime, rng: random.Random) -> list[OrderTimelineEntry]:
    """Produce a timeline whose final entry is always the order's current status."""
    steps = STATUS_FLOW[status]
    entries: list[OrderTimelineEntry] = []
    at = created_at
    for step in steps:
        note_vi, note_en = STATUS_NOTES[step]
        entries.append(
            OrderTimelineEntry(status=step, at=at, note_vi=note_vi, note_en=note_en)  # type: ignore[arg-type]
        )
        at = at + timedelta(hours=rng.randint(4, 30))
    return entries


def _make_name(rng: random.Random) -> str:
    return f"{rng.choice(FAMILY_NAMES)} {rng.choice(MIDDLE_NAMES)} {rng.choice(GIVEN_NAMES)}"


def _shipping_fee(subtotal: int, rng: random.Random) -> int:
    """Mirrors shipping-policy.md: free above 500,000 VND, otherwise by region."""
    if subtotal >= 500_000:
        return 0
    return rng.choice([25_000, 35_000, 55_000])


def _build_order(
    *,
    order_code: str,
    name: str,
    phone: str,
    email: str,
    items: list[OrderItem],
    status: str,
    created_at: datetime,
    rng: random.Random,
) -> Order:
    subtotal = sum(item.unit_price * item.quantity for item in items)
    shipping_fee = _shipping_fee(subtotal, rng)
    timeline = _build_timeline(status, created_at, rng)

    in_transit = status in {"shipped", "out_for_delivery"}
    completed = status in {"delivered", "returned"}

    carrier = rng.choice(CARRIERS) if (in_transit or completed) else None
    tracking = f"{rng.randint(10**11, 10**12 - 1)}" if carrier else None

    estimated_delivery = None
    if in_transit:
        estimated_delivery = timeline[-1].at + timedelta(days=rng.randint(1, 4))

    normalized_phone = normalize_phone(phone)

    return Order(
        order_code=order_code,
        customer_name=name,
        phone_hash=hash_phone(phone),
        phone_last4=normalized_phone[-4:],
        email_hash=hash_email(email),
        items=items,
        subtotal=subtotal,
        shipping_fee=shipping_fee,
        total=subtotal + shipping_fee,
        status=status,  # type: ignore[arg-type]
        timeline=timeline,
        carrier=carrier,
        tracking_code=tracking,
        estimated_delivery=estimated_delivery,
        created_at=created_at,
    )


def _item_from_product(product, quantity: int) -> OrderItem:
    return OrderItem(
        sku=product.sku,
        name_vi=product.name_vi,
        name_en=product.name_en,
        quantity=quantity,
        unit_price=product.effective_price,
    )


# Fixed cases the demo and the eval dataset reference by code.
# (order_code, phone, email, status, product SKUs, days ago)
SHOWCASE = [
    ("DH2026090001", "0901234567", "an.nguyen@example.com", "out_for_delivery",
     ["PHN-001", "AUD-002"], 4),
    ("DH2026090002", "0912345678", "binh.tran@example.com", "delivered",
     ["LAP-002"], 12),
    ("DH2026090003", "0987654321", "chi.le@example.com", "cancelled",
     ["TAB-001", "ACC-001"], 8),
    ("DH2026090004", "0938111222", "dung.pham@example.com", "returned",
     ["KIT-002"], 25),
    ("DH2026090005", "0977333444", "ha.hoang@example.com", "packing",
     ["TVS-000", "ACC-002", "WAT-000"], 1),
]

SHOWCASE_NAMES = {
    "DH2026090001": "Nguyễn Văn An",
    "DH2026090002": "Trần Thị Bình",
    "DH2026090003": "Lê Ngọc Chi",
    "DH2026090004": "Phạm Hữu Dũng",
    "DH2026090005": "Hoàng Thanh Hà",
}


def generate_orders(bulk_count: int = BULK_COUNT) -> list[Order]:
    rng = random.Random(SEED)
    products = generate_products()
    by_sku = {product.sku: product for product in products}
    orders: list[Order] = []

    for order_code, phone, email, status, skus, days_ago in SHOWCASE:
        items = [_item_from_product(by_sku[sku], 1) for sku in skus]
        orders.append(
            _build_order(
                order_code=order_code,
                name=SHOWCASE_NAMES[order_code],
                phone=phone,
                email=email,
                items=items,
                status=status,
                created_at=NOW - timedelta(days=days_ago),
                rng=rng,
            )
        )

    # Most orders in a real system are already delivered. Weighting matters
    # because the dashboard's status breakdown should look plausible.
    statuses = (
        ["delivered"] * 55
        + ["shipped"] * 10
        + ["out_for_delivery"] * 6
        + ["packing"] * 6
        + ["confirmed"] * 5
        + ["pending"] * 4
        + ["cancelled"] * 8
        + ["returned"] * 6
    )

    for index in range(bulk_count):
        status = rng.choice(statuses)
        item_count = rng.choices([1, 2, 3], weights=[62, 28, 10])[0]
        chosen = rng.sample(products, item_count)
        items = [_item_from_product(p, rng.choices([1, 2], weights=[85, 15])[0]) for p in chosen]

        digits = f"09{rng.randint(10**7, 10**8 - 1)}"
        name = _make_name(rng)
        slug = f"kh{index:04d}"

        orders.append(
            _build_order(
                order_code=f"DH2026{rng.randint(1, 9):02d}{index + 100:05d}",
                name=name,
                phone=digits,
                email=f"{slug}@example.com",
                items=items,
                status=status,
                created_at=NOW - timedelta(days=rng.randint(1, 90), hours=rng.randint(0, 23)),
                rng=rng,
            )
        )

    return orders


if __name__ == "__main__":
    generated = generate_orders()
    print(f"Generated {len(generated)} orders\n")

    counts: dict[str, int] = {}
    for order in generated:
        counts[order.status] = counts.get(order.status, 0) + 1
    for status, total in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status:<18} {total:>4}")

    print("\nShowcase orders:")
    for order in generated[: len(SHOWCASE)]:
        total = f"{order.total:,}".replace(",", ".")
        print(f"  {order.order_code}  {order.customer_name:<18} {order.status:<17} "
              f"{total:>11} d  {len(order.timeline)} steps  ****{order.phone_last4}")

    codes = [o.order_code for o in generated]
    print("\nOrder codes unique:", len(set(codes)) == len(codes))
