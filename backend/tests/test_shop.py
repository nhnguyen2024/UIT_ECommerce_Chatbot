"""Guest checkout: validation, fees, and that the server sets the price."""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.api import shop
from app.db import schema
from app.geo import FREE_SHIPPING_FROM, nearest_warehouse, shipping_fee
from app.security import hash_phone


def request(**overrides) -> dict:
    return {
        "items": [{"sku": "AUD-002", "quantity": 1}],
        "name": "Nguyễn Văn Test",
        "phone": "0901 234 567",
        "email": "Test@Example.com",
        "province": "danang",
        "payment_method": "cod",
        **overrides,
    }


class TestValidation:
    def test_contacts_are_normalised(self):
        checkout = shop.CheckoutRequest(**request(phone="+84 901 234 567"))
        assert checkout.phone == "0901234567"
        assert checkout.email == "test@example.com"

    @pytest.mark.parametrize("phone", ["12345", "0901234", "abcdefghij"])
    def test_a_non_vietnamese_phone_is_rejected(self, phone):
        with pytest.raises(ValidationError):
            shop.CheckoutRequest(**request(phone=phone))

    def test_an_unknown_province_is_rejected(self):
        with pytest.raises(ValidationError):
            shop.CheckoutRequest(**request(province="atlantis"))

    def test_the_client_cannot_send_a_price(self):
        checkout = shop.CheckoutRequest(**request(items=[{"sku": "AUD-002", "quantity": 1, "price": 1}]))
        assert not hasattr(checkout.items[0], "price")

    def test_an_empty_cart_is_rejected(self):
        with pytest.raises(ValidationError):
            shop.CheckoutRequest(**request(items=[]))


class TestShippingFees:
    """Mirrors shipping-policy.md, "Shipping fees"."""

    def test_inner_city(self):
        assert shipping_fee("hanoi", 100_000) == 25_000
        assert shipping_fee("hcm", 100_000) == 25_000

    def test_other_provinces(self):
        assert shipping_fee("danang", 100_000) == 35_000

    def test_mountainous(self):
        assert shipping_fee("laocai", 100_000) == 55_000

    def test_free_from_the_threshold(self):
        assert shipping_fee("laocai", FREE_SHIPPING_FROM) == 0
        assert shipping_fee("laocai", FREE_SHIPPING_FROM - 1000) == 55_000

    def test_the_north_ships_from_hanoi(self):
        assert nearest_warehouse("haiphong") == "wh-hanoi"
        assert nearest_warehouse("cantho") == "wh-hcm"


class FakeProducts:
    def __init__(self, stock: dict[str, int]):
        self.stock = stock
        self.price = {sku: 1_290_000 for sku in stock}

    async def find_one_and_update(self, query, update, projection=None):
        sku, needed = query["sku"], query["stock"]["$gte"]
        if self.stock.get(sku, 0) < needed:
            return None
        self.stock[sku] -= needed
        return {"sku": sku, "name_vi": sku, "name_en": sku, "price": self.price[sku], "sale_price": None}

    async def update_one(self, query, update):
        self.stock[query["sku"]] += update["$inc"]["stock"]


class FakeOrders:
    def __init__(self, collisions: int = 0):
        self.saved: list[dict] = []
        self.collisions = collisions

    async def insert_one(self, document):
        if self.collisions:
            self.collisions -= 1
            raise DuplicateKeyError("taken")
        self.saved.append(document)


def fake_db(monkeypatch, stock, collisions=0):
    products, orders = FakeProducts(stock), FakeOrders(collisions)
    monkeypatch.setattr(shop, "get_db", lambda: {schema.PRODUCTS: products, schema.ORDERS: orders})
    return products, orders


class TestPlacingAnOrder:
    async def test_price_comes_from_the_database(self, monkeypatch):
        products, orders = fake_db(monkeypatch, {"AUD-002": 5})
        result = await shop.place_order(shop.CheckoutRequest(**request()))
        assert result["subtotal"] == 1_290_000
        assert result["shipping_fee"] == 0
        assert products.stock["AUD-002"] == 4

    async def test_order_is_trackable_like_any_website_order(self, monkeypatch):
        _, orders = fake_db(monkeypatch, {"AUD-002": 5})
        result = await shop.place_order(shop.CheckoutRequest(**request()))
        saved = orders.saved[0]
        assert saved["order_code"] == result["order_code"]
        assert saved["channel"] == "website"
        assert saved["status"] == "pending"
        assert saved["phone_hash"] == hash_phone("0901234567")
        assert [stop["place"] for stop in saved["route"]] == ["wh-hcm", "hub-south", "hub-central", "danang"]
        assert saved["route"][0]["arrived_at"] is not None
        assert all(stop["arrived_at"] is None for stop in saved["route"][1:])

    async def test_no_readable_contact_is_stored_or_returned(self, monkeypatch):
        _, orders = fake_db(monkeypatch, {"AUD-002": 5})
        result = await shop.place_order(shop.CheckoutRequest(**request()))
        stored = str(orders.saved[0])
        assert "0901234567" not in stored and "test@example.com" not in stored
        assert result["phone_masked"] == "******4567"

    async def test_out_of_stock_rolls_back_the_whole_cart(self, monkeypatch):
        products, orders = fake_db(monkeypatch, {"AUD-002": 5, "PHN-001": 0})
        items = [{"sku": "AUD-002", "quantity": 2}, {"sku": "PHN-001", "quantity": 1}]
        with pytest.raises(HTTPException) as caught:
            await shop.place_order(shop.CheckoutRequest(**request(items=items)))
        assert caught.value.status_code == 409
        assert products.stock == {"AUD-002": 5, "PHN-001": 0}
        assert orders.saved == []

    async def test_repeated_lines_are_merged_before_the_limit(self, monkeypatch):
        fake_db(monkeypatch, {"AUD-002": 50})
        items = [{"sku": "AUD-002", "quantity": 6}, {"sku": "aud-002", "quantity": 6}]
        with pytest.raises(HTTPException) as caught:
            await shop.place_order(shop.CheckoutRequest(**request(items=items)))
        assert caught.value.status_code == 422

    async def test_a_code_collision_is_retried(self, monkeypatch):
        _, orders = fake_db(monkeypatch, {"AUD-002": 5}, collisions=2)
        result = await shop.place_order(shop.CheckoutRequest(**request()))
        assert orders.saved[0]["order_code"] == result["order_code"]
