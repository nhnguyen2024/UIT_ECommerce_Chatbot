"""Twelve months of synthetic history for the analytics pipeline.

    python analytics/simulate.py            # writes analytics/out/raw/...

The live system is weeks old, so it has too little history to analyse, and no
marketplace API is connected. This generates the missing history, clearly
labelled: every record carries `is_simulated: true`.

What is generated, and in what shape:

- source=oms          The seller's consolidated order system: every order from
                      every channel, with the hashed contact the chatbot verifies
                      against. Same shape as the app's `orders` collection.
- source=shopee|lazada|tiktok_shop
                      Order, review and listing-stats extracts in each platform's
                      own API vocabulary (field names, status codes, epoch vs ISO
                      times, fee fields). Silver has to normalise them, which is
                      the real work in a multi-channel pipeline.
- source=website      Storefront clickstream: searches, product views, carts,
                      checkouts.
- source=chatbot      Turn telemetry and the shopper's (masked) question text.

The generator plants a few relationships on purpose (late deliveries lower
ratings and repeat purchases; some asked-for products are not stocked; one
carrier is slow in central provinces; instalment questions escalate). They let
the gold layer demonstrate that it recovers real patterns. The report says so;
nothing here is presented as a finding about a real business.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.geo import DESTINATIONS, PLACES, nearest_warehouse, shipping_fee  # noqa: E402
from app.security import hash_email, hash_phone  # noqa: E402
from seed.catalog import generate_products  # noqa: E402

SEED = 20260922
NOW = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)  # same "now" as the seed data
START = NOW - timedelta(days=365)
EXTRACT_DATE = "2026-09-22"
OUT = ROOT / "out" / "raw"

N_CUSTOMERS = 3000
CHANNELS = ["website", "shopee", "lazada", "tiktok_shop"]
CHANNEL_WEIGHTS = [22, 38, 22, 18]
CARRIERS = ["Giao Hàng Nhanh", "Giao Hàng Tiết Kiệm", "Viettel Post", "J&T Express"]
PLATFORM_FEE = {"website": 0.0, "shopee": 0.12, "lazada": 0.10, "tiktok_shop": 0.09}  # commission + service, assumed

# Things shoppers ask the chatbot for that the catalogue does not carry. The
# gold layer should surface these as demand gaps.
UNMET_QUERIES = [
    ("máy chơi game cầm tay", "gaming handheld", 0.20),
    ("camera an ninh wifi", "wifi security camera", 0.18),
    ("máy lọc không khí", "air purifier", 0.16),
    ("tai nghe có dây type-c", "wired usb-c earphones", 0.14),
    ("sạc dự phòng 30000mAh", "30000mAh power bank", 0.12),
    ("máy in mini", "mini printer", 0.08),
    ("bàn phím cơ", "mechanical keyboard", 0.12),
]
POLICY_TOPICS = [  # topic, share, covered by the published policies?
    ("return_window", 0.26, True),
    ("warranty", 0.20, True),
    ("shipping_fee", 0.16, True),
    ("refund_timing", 0.14, True),
    ("marketplace_return", 0.10, True),
    ("installment", 0.08, False),   # "trả góp": no policy covers it
    ("vat_invoice", 0.06, True),
]
REVIEW_THEMES = {
    "delivery_slow": ["Giao hàng chậm quá", "Chờ lâu hơn dự kiến", "Shipper giao trễ mấy ngày"],
    "packaging": ["Đóng gói cẩn thận", "Hộp bị móp nhẹ", "Đóng gói sơ sài"],
    "quality_good": ["Sản phẩm dùng tốt", "Chất lượng ổn so với giá", "Hàng chính hãng, rất hài lòng"],
    "battery": ["Pin hơi yếu", "Pin trâu, dùng cả ngày", "Pin tụt nhanh"],
    "sound": ["Âm thanh hay", "Bass hơi yếu", "Chống ồn tốt"],
    "support": ["Shop hỗ trợ nhiệt tình", "Hỏi chatbot trả lời nhanh", "Liên hệ mãi mới được hỗ trợ"],
}
STATUS_MAP = {
    "shopee": {"delivered": "COMPLETED", "cancelled": "CANCELLED", "returned": "TO_RETURN",
               "shipped": "SHIPPED", "out_for_delivery": "SHIPPED", "packing": "READY_TO_SHIP",
               "confirmed": "READY_TO_SHIP", "pending": "UNPAID"},
    "lazada": {"delivered": "delivered", "cancelled": "canceled", "returned": "returned",
               "shipped": "shipped", "out_for_delivery": "shipped", "packing": "ready_to_ship",
               "confirmed": "packed", "pending": "pending"},
    "tiktok_shop": {"delivered": 130, "cancelled": 140, "returned": 140, "shipped": 121,
                    "out_for_delivery": 122, "packing": 112, "confirmed": 111, "pending": 100},
}

rng = random.Random(SEED)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def weighted(options, weights):
    return rng.choices(options, weights=weights)[0]


def mask_text(text: str) -> str:
    """Analytics never receives a readable phone number or email."""
    import re
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", text)
    return re.sub(r"(\+?84|0)[\d\s.-]{8,12}\d", "<phone>", text)


# --- customers -----------------------------------------------------------------
def make_customers(products):
    customers = []
    for i in range(N_CUSTOMERS):
        phone = f"09{rng.randint(10**7, 10**8 - 1)}"
        province = weighted(DESTINATIONS, [22 if d == "hcm" else 20 if d == "hanoi" else 3 for d in DESTINATIONS])
        customers.append({
            "idx": i,
            "customer_key": hash_phone(phone),
            "email_hash": hash_email(f"cust{i:05d}@example.com"),
            "province": province,
            "home_channel": weighted(CHANNELS, CHANNEL_WEIGHTS),
            "affinity": weighted(sorted({p.category for p in products}), None),
            # Latent loyalty; the analysis never sees it directly.
            "loyalty": rng.betavariate(2, 3),
            "uses_chatbot": rng.random() < 0.35,
            "signup": START + timedelta(days=rng.randint(0, 330)),
        })
    return customers


# --- orders ----------------------------------------------------------------------
def promised_days(province):
    return 2 if province in ("hanoi", "hcm") else 4 if PLACES[province].region != "central" else 5


def delivery_days(province, carrier):
    base = promised_days(province) + rng.choice([-1, 0, 0, 0, 1])
    # Planted: J&T is slow in central provinces.
    if carrier == "J&T Express" and PLACES[province].region == "central":
        base += rng.choice([2, 3, 4])
    if rng.random() < 0.08:
        base += rng.randint(2, 5)  # random disruption
    return max(1, base)


def make_orders(customers, products):
    by_cat = defaultdict(list)
    for p in products:
        by_cat[p.category].append(p)
    orders, seq = [], 0
    for c in customers:
        t = c["signup"] + timedelta(hours=rng.randint(1, 240))
        last_bad = False
        while t < NOW:
            channel = c["home_channel"] if rng.random() < 0.7 else weighted(CHANNELS, CHANNEL_WEIGHTS)
            cat = c["affinity"] if rng.random() < 0.55 else rng.choice(list(by_cat))
            n_items = rng.choices([1, 2, 3], weights=[70, 24, 6])[0]
            items = []
            for p in rng.sample(by_cat[cat], min(n_items, len(by_cat[cat]))):
                price = p.effective_price
                if channel != "website":
                    price = int(round(price * rng.uniform(0.97, 1.03), -3))
                items.append({"sku": p.sku, "name_vi": p.name_vi, "name_en": p.name_en,
                              "category": p.category, "quantity": 1 if rng.random() < 0.9 else 2,
                              "unit_price": price, "list_price": p.price,
                              "discounted": p.sale_price is not None})
            subtotal = sum(i["unit_price"] * i["quantity"] for i in items)
            carrier = rng.choice(CARRIERS)
            status = weighted(["delivered", "cancelled", "returned"],
                              [86, 12 if channel == "tiktok_shop" else 7, 4])
            days = delivery_days(c["province"], carrier)
            late = days > promised_days(c["province"])
            seq += 1
            code = f"DH{t:%Y%m}H{seq:05d}"
            order = {
                "order_code": code,
                "channel": channel,
                "channel_order_code": None if channel == "website" else f"{channel[:2].upper()}{seq:09d}",
                "customer_key": c["customer_key"],
                "province": c["province"],
                "warehouse": nearest_warehouse(c["province"]),
                "items": items,
                "subtotal": subtotal,
                "shipping_fee": shipping_fee(c["province"], subtotal) if channel == "website" else 0,
                "status": status,
                "carrier": carrier if status != "cancelled" else None,
                "created_at": iso(t),
                "shipped_at": iso(t + timedelta(hours=rng.randint(6, 30))) if status != "cancelled" else None,
                "delivered_at": iso(t + timedelta(days=days, hours=rng.randint(0, 10))) if status in ("delivered", "returned") else None,
                "promised_days": promised_days(c["province"]),
                "delivery_days": days if status in ("delivered", "returned") else None,
                "is_simulated": True,
            }
            order["total"] = subtotal + order["shipping_fee"]
            orders.append(order)
            bad = late or status == "returned"
            last_bad = bad
            # Planted: repeat purchase depends on loyalty and on the last experience.
            p_repeat = 0.25 + 0.6 * c["loyalty"] - (0.22 if last_bad else 0) + (0.06 if c["uses_chatbot"] else 0)
            if rng.random() > max(0.03, p_repeat):
                break
            t += timedelta(days=rng.expovariate(1 / 55) + 3)
    return orders


# --- marketplace API extracts -------------------------------------------------------
def epoch(iso_s):
    return int(datetime.fromisoformat(iso_s).timestamp())


def shopee_order(o):
    return {
        "order_sn": o["channel_order_code"], "order_status": STATUS_MAP["shopee"][o["status"]],
        "create_time": epoch(o["created_at"]), "update_time": epoch(o["delivered_at"] or o["created_at"]),
        "buyer_user_id": int(hashlib.md5(o["customer_key"].encode()).hexdigest()[:9], 16),
        "region": "VN", "currency": "VND", "total_amount": o["total"],
        "shipping_carrier": o["carrier"], "days_to_ship": 2,
        "item_list": [{"item_sku": i["sku"], "model_quantity_purchased": i["quantity"],
                       "model_original_price": i["list_price"], "model_discounted_price": i["unit_price"]} for i in o["items"]],
        "escrow": {"commission_fee": round(o["subtotal"] * 0.08), "service_fee": round(o["subtotal"] * 0.04),
                   "voucher_from_seller": 0},
        "is_simulated": True,
    }


def lazada_order(o):
    return {
        "order_id": int(o["channel_order_code"][2:]), "order_number": o["channel_order_code"],
        "created_at": o["created_at"].replace("+00:00", " +0000"), "statuses": [STATUS_MAP["lazada"][o["status"]]],
        "price": f"{o['total']}.00", "shipping_provider": o["carrier"], "customer_id": o["customer_key"][:16],
        "items": [{"sku": i["sku"], "item_price": float(i["unit_price"]), "paid_price": float(i["unit_price"]),
                   "quantity": i["quantity"]} for i in o["items"]],
        "fees": {"commission": round(o["subtotal"] * 0.07), "payment_fee": round(o["subtotal"] * 0.03)},
        "is_simulated": True,
    }


def tiktok_order(o):
    return {
        "id": o["channel_order_code"], "status": STATUS_MAP["tiktok_shop"][o["status"]],
        "create_time": epoch(o["created_at"]), "user_id": o["customer_key"][-12:],
        "payment": {"currency": "VND", "total_amount": str(o["total"]), "platform_discount": "0",
                    "seller_discount": "0", "sub_total": str(o["subtotal"])},
        "line_items": [{"seller_sku": i["sku"], "sale_price": str(i["unit_price"]),
                        "original_price": str(i["list_price"])} for i in o["items"] for _ in range(i["quantity"])],
        "shipping_provider": o["carrier"], "fees": {"commission": round(o["subtotal"] * 0.09)},
        "is_simulated": True,
    }


def reviews_for(orders):
    out = []
    for o in orders:
        if o["channel"] == "website" or o["status"] != "delivered" or rng.random() > 0.38:
            continue
        late = o["delivery_days"] > o["promised_days"]
        rating = rng.choices([5, 4, 3, 2, 1], weights=[18, 20, 22, 22, 18] if late else [58, 27, 9, 4, 2])[0]
        themes = (["delivery_slow"] if late else []) + rng.sample([t for t in REVIEW_THEMES if t != "delivery_slow"], 1)
        text = ". ".join(rng.choice(REVIEW_THEMES[t]) for t in themes)
        sku = o["items"][0]["sku"]
        at = datetime.fromisoformat(o["delivered_at"]) + timedelta(days=rng.randint(0, 6))
        base = {"is_simulated": True, "sku": sku, "rating": rating, "comment": text}
        if o["channel"] == "shopee":
            out.append(("shopee", {**base, "order_sn": o["channel_order_code"], "rating_star": rating,
                                   "create_time": epoch(iso(at))}))
        elif o["channel"] == "lazada":
            out.append(("lazada", {**base, "order_id": o["channel_order_code"], "product_rating": rating,
                                   "review_time": iso(at)}))
        else:
            out.append(("tiktok_shop", {**base, "order_id": o["channel_order_code"], "star": rating,
                                        "create_time": epoch(iso(at))}))
    return out


def listing_stats(products, orders):
    sold = defaultdict(int)
    for o in orders:
        if o["channel"] != "website":
            week = datetime.fromisoformat(o["created_at"]).strftime("%G-W%V")
            for i in o["items"]:
                sold[(o["channel"], i["sku"], week)] += i["quantity"]
    rows = []
    weeks = sorted({datetime.fromisoformat(iso(START + timedelta(days=7 * k))).strftime("%G-W%V") for k in range(53)})
    listed = {p.sku: p.listed_on for p in products}
    for p in products:
        for ch in ("shopee", "lazada", "tiktok_shop"):
            if ch not in listed[p.sku]:
                continue
            for w in weeks:
                units = sold.get((ch, p.sku, w), 0)
                views = int(units * rng.uniform(25, 60) + rng.randint(5, 60))
                rows.append((ch, {"sku": p.sku, "week": w, "views": views, "clicks": int(views * rng.uniform(0.08, 0.2)),
                                  "units": units, "is_simulated": True}))
    return rows


# --- website clickstream -----------------------------------------------------------------
def web_events(customers, products, orders):
    events, sid = [], 0
    by_customer = defaultdict(list)
    for o in orders:
        if o["channel"] == "website":
            by_customer[o["customer_key"]].append(o)
    skus = [p.sku for p in products]
    for c in customers:
        n_sessions = rng.randint(1, 8) + 3 * len(by_customer[c["customer_key"]])
        for _ in range(n_sessions):
            sid += 1
            t = START + timedelta(minutes=rng.randint(0, 365 * 24 * 60))
            session = f"w{sid:07d}"
            viewed = rng.sample(skus, rng.randint(1, 5))
            if rng.random() < 0.4:
                q, _, _ = rng.choice(UNMET_QUERIES) if rng.random() < 0.25 else (rng.choice(products).name_vi.split()[0], None, None)
                events.append({"session_id": session, "customer_key": c["customer_key"], "at": iso(t),
                               "event": "search", "query": q, "is_simulated": True})
            for s in viewed:
                t += timedelta(seconds=rng.randint(10, 120))
                events.append({"session_id": session, "customer_key": c["customer_key"], "at": iso(t),
                               "event": "view_product", "sku": s, "is_simulated": True})
            if rng.random() < 0.18:
                t += timedelta(seconds=40)
                events.append({"session_id": session, "customer_key": c["customer_key"], "at": iso(t),
                               "event": "add_to_cart", "sku": viewed[0], "is_simulated": True})
                if rng.random() < 0.45:
                    events.append({"session_id": session, "customer_key": c["customer_key"],
                                   "at": iso(t + timedelta(minutes=3)), "event": "begin_checkout", "is_simulated": True})
    for o in orders:
        if o["channel"] == "website":
            events.append({"session_id": f"p{o['order_code']}", "customer_key": o["customer_key"], "at": o["created_at"],
                           "event": "purchase", "order_code": o["order_code"], "value": o["total"], "is_simulated": True})
    return events


# --- chatbot telemetry -----------------------------------------------------------------------
def chat_turns(customers, products, orders):
    turns, n = [], 0
    by_customer = defaultdict(list)
    for o in orders:
        by_customer[o["customer_key"]].append(o)
    out_of_stock = [p for p in products if p.stock == 0]

    def turn(c, at, intent, text, **kw):
        nonlocal n
        n += 1
        base = {"session_id": f"c{n:07d}", "customer_key": c["customer_key"] if c else None, "at": iso(at),
                "lang": "vi" if rng.random() < 0.8 else "en", "intent": intent, "text": mask_text(text),
                "tools_used": kw.get("tools", []), "latency_ms": int(rng.gauss(11500, 2500)),
                "input_tokens": rng.randint(3000, 9000), "output_tokens": rng.randint(150, 700),
                "cache_read_tokens": rng.randint(0, 6000), "cost_usd": round(rng.uniform(0.0012, 0.0035), 5),
                "grounded": kw.get("grounded", rng.random() > 0.03), "blocked": kw.get("blocked", False),
                "escalated": kw.get("escalated", False), "topic": kw.get("topic"), "sku": kw.get("sku"),
                "unmet_query": kw.get("unmet"), "budget_vnd": kw.get("budget"),
                "verified_order_code": kw.get("order"), "is_simulated": True}
        turns.append(base)

    for c in customers:
        if not c["uses_chatbot"]:
            continue
        for o in by_customer[c["customer_key"]]:
            created = datetime.fromisoformat(o["created_at"])
            late = (o["delivery_days"] or 0) > o["promised_days"]
            # Planted: late parcels generate tracking questions, and some escalations.
            if rng.random() < (0.85 if late else 0.35):
                turn(c, created + timedelta(days=o["promised_days"] + (1 if late else 0)), "order_tracking",
                     f"Đơn {o['channel_order_code'] or o['order_code']} của mình tới đâu rồi? sđt 0901234567",
                     tools=["get_order_status"], order=o["order_code"],
                     escalated=late and rng.random() < 0.25)
            if rng.random() < 0.3:
                topic, _, covered = rng.choices(POLICY_TOPICS, weights=[t[1] for t in POLICY_TOPICS])[0]
                turn(c, created - timedelta(hours=rng.randint(1, 72)), "policy_question",
                     {"return_window": "Đổi trả trong bao lâu?", "warranty": "Bảo hành mấy tháng?",
                      "shipping_fee": "Phí ship bao nhiêu?", "refund_timing": "Bao lâu thì hoàn tiền?",
                      "marketplace_return": "Mua trên Shopee thì trả hàng thế nào?",
                      "installment": "Shop có hỗ trợ trả góp không?", "vat_invoice": "Có xuất hoá đơn VAT không?"}[topic],
                     tools=["search_policies"], topic=topic,
                     grounded=covered or rng.random() < 0.3, escalated=(not covered) and rng.random() < 0.6)
        for _ in range(rng.randint(0, 3)):
            at = START + timedelta(minutes=rng.randint(0, 365 * 24 * 60))
            r = rng.random()
            if r < 0.22:
                q_vi, q_en, _ = rng.choices(UNMET_QUERIES, weights=[u[2] for u in UNMET_QUERIES])[0]
                turn(c, at, "product_consultation", f"Shop có bán {q_vi} không?", tools=["search_products"],
                     unmet=q_en, grounded=True)
            elif r < 0.32 and out_of_stock:
                p = rng.choice(out_of_stock)
                turn(c, at, "product_consultation", f"{p.name_vi} còn hàng không?", tools=["get_product_details"], sku=p.sku)
            else:
                p = rng.choice(products)
                budget = rng.choice([1_000_000, 2_000_000, 3_000_000, 5_000_000, 10_000_000, 20_000_000])
                turn(c, at, "product_consultation", f"Tư vấn {p.category_vi.lower()} dưới {budget // 1_000_000} triệu",
                     tools=["search_products"], sku=p.sku, budget=budget)
    # Anonymous visitors: smalltalk, out of scope, injection attempts.
    for _ in range(900):
        at = START + timedelta(minutes=rng.randint(0, 365 * 24 * 60))
        kind = weighted(["smalltalk", "out_of_scope", "human_request"], [40, 45, 15])
        turn(None, at, kind, {"smalltalk": "Chào shop", "out_of_scope": "Shop có bán áo khoác không?",
                              "human_request": "Cho mình gặp nhân viên"}[kind],
             tools=["create_handoff"] if kind == "human_request" else [], escalated=kind == "human_request",
             blocked=kind == "out_of_scope")
    return turns


# --- writing -------------------------------------------------------------------------------------
def write(source, dataset, rows):
    path = OUT / f"source={source}" / dataset / f"dt={EXTRACT_DATE}"
    path.mkdir(parents=True, exist_ok=True)
    with open(path / "part-000.json", "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def main():
    products = generate_products()
    customers = make_customers(products)
    orders = make_orders(customers, products)
    counts = {}
    counts["oms/orders"] = write("oms", "orders", orders)
    counts["oms/customers"] = write("oms", "customers", [
        {"customer_key": c["customer_key"], "email_hash": c["email_hash"], "province": c["province"],
         "first_seen": iso(c["signup"]), "is_simulated": True} for c in customers])
    for ch, builder in (("shopee", shopee_order), ("lazada", lazada_order), ("tiktok_shop", tiktok_order)):
        counts[f"{ch}/orders"] = write(ch, "orders", [builder(o) for o in orders if o["channel"] == ch])
    reviews = defaultdict(list)
    for ch, row in reviews_for(orders):
        reviews[ch].append(row)
    for ch, rows in reviews.items():
        counts[f"{ch}/reviews"] = write(ch, "reviews", rows)
    stats = defaultdict(list)
    for ch, row in listing_stats(products, orders):
        stats[ch].append(row)
    for ch, rows in stats.items():
        counts[f"{ch}/listing_stats"] = write(ch, "listing_stats", rows)
    counts["website/events"] = write("website", "events", web_events(customers, products, orders))
    counts["chatbot/turns"] = write("chatbot", "turns", chat_turns(customers, products, orders))
    for k, v in counts.items():
        print(f"  {k:<28} {v:>8,}")


if __name__ == "__main__":
    main()
