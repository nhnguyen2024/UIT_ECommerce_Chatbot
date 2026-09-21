"""Order routes, and the tracking map data the order tool hands the UI."""


import pytest

from app.agent.tools import orders as order_tools
from app.agent.tools.base import ToolContext
from app.db import schema
from app.geo import DESTINATIONS, PLACES, plan_route
from seed.orders import NOW, generate_orders

ORDERS = generate_orders()
LEFT_WAREHOUSE = {"shipped", "out_for_delivery", "delivered", "returned"}


class TestPlaces:
    def test_every_place_is_inside_vietnam_s_bounding_box(self):
        for place in PLACES.values():
            assert 8.4 <= place.lat <= 23.4 and 102.1 <= place.lon <= 109.5, place.key

    def test_route_from_south_to_north_passes_the_central_hub(self):
        assert plan_route("wh-hcm", "hanoi") == ["wh-hcm", "hub-south", "hub-central", "hub-north", "hanoi"]

    def test_route_within_one_region_does_not_repeat_its_hub(self):
        assert plan_route("wh-hanoi", "haiphong") == ["wh-hanoi", "hub-north", "haiphong"]

    @pytest.mark.parametrize("destination", DESTINATIONS)
    def test_every_route_starts_at_a_warehouse_and_ends_at_its_destination(self, destination):
        for warehouse in ("wh-hanoi", "wh-hcm"):
            route = plan_route(warehouse, destination)
            assert route[0] == warehouse
            assert route[-1] == destination
            assert all(PLACES[stop].kind == "hub" for stop in route[1:-1])


class TestSeededRoutes:
    def test_every_order_has_a_route_to_its_destination(self):
        for order in ORDERS:
            assert order.destination in DESTINATIONS
            assert order.route[-1].place == order.destination
            assert PLACES[order.route[0].place].kind == "warehouse"

    def test_reached_stops_come_before_stops_still_ahead(self):
        for order in ORDERS:
            reached = [stop.arrived_at is not None for stop in order.route]
            assert reached == sorted(reached, reverse=True), order.order_code

    def test_arrivals_are_chronological_and_not_in_the_future(self):
        for order in ORDERS:
            times = [stop.arrived_at for stop in order.route if stop.arrived_at is not None]
            assert times == sorted(times), order.order_code
            if order.status == "shipped":
                assert all(time <= NOW for time in times), order.order_code

    def test_orders_still_at_the_warehouse_have_reached_only_the_warehouse(self):
        for order in ORDERS:
            if order.status not in LEFT_WAREHOUSE:
                assert [stop.arrived_at is not None for stop in order.route][:2] == [True, False]

    def test_destination_is_reached_only_once_delivered(self):
        for order in ORDERS:
            reached = order.route[-1].arrived_at is not None
            assert reached == (order.status in {"delivered", "returned"}), order.order_code

    def test_out_for_delivery_has_passed_every_hub(self):
        for order in ORDERS:
            if order.status == "out_for_delivery":
                assert all(stop.arrived_at for stop in order.route[:-1]), order.order_code

    def test_delivery_matches_the_timeline(self):
        for order in ORDERS:
            delivered = [entry.at for entry in order.timeline if entry.status == "delivered"]
            if delivered:
                assert order.route[-1].arrived_at == delivered[0]

    def test_demo_order_crosses_the_country(self):
        demo = next(order for order in ORDERS if order.order_code == "DH2026090001")
        assert [stop.place for stop in demo.route] == ["wh-hcm", "hub-south", "hub-central", "hub-north", "hanoi"]


class FakeCollection:
    def __init__(self, document):
        self.document = document

    async def find_one(self, query, projection=None):
        return self.document


def order_document(code: str) -> dict:
    order = next(order for order in ORDERS if order.order_code == code)
    return order.model_dump()


async def look_up(monkeypatch, contact: str, lang: str = "vi"):
    document = order_document("DH2026090001")
    monkeypatch.setattr(order_tools, "get_db", lambda: {schema.ORDERS: FakeCollection(document)})
    return await order_tools.get_order_status(
        context=ToolContext(session_id="s", lang=lang), order_code="DH2026090001", contact=contact
    )


class TestOrderTool:
    async def test_verified_lookup_returns_the_route_for_the_map(self, monkeypatch):
        result = await look_up(monkeypatch, "0901234567")
        stops = result.tracking["stops"]
        assert [stop["key"] for stop in stops] == ["wh-hcm", "hub-south", "hub-central", "hub-north", "hanoi"]
        assert all(isinstance(stop["lat"], float) for stop in stops)
        assert stops[-1]["arrived_at"] is None

    async def test_model_gets_place_names_and_where_the_parcel_is(self, monkeypatch):
        result = await look_up(monkeypatch, "0901234567")
        assert result.data["destination"] == "Hà Nội"
        assert result.data["current_location"] == "Trung tâm phân loại Hà Nội"
        assert "lat" not in result.data["route"][0]

    async def test_place_names_follow_the_shopper_s_language(self, monkeypatch):
        result = await look_up(monkeypatch, "0901234567", lang="en")
        assert result.data["current_location"] == "Hanoi sorting hub"

    async def test_failed_verification_returns_no_route(self, monkeypatch):
        result = await look_up(monkeypatch, "0900000000")
        assert result.tracking is None
        assert "route" not in result.data

    async def test_destination_is_a_province_not_an_address(self, monkeypatch):
        result = await look_up(monkeypatch, "0901234567")
        assert set(result.tracking["stops"][-1]) == {"key", "name", "kind", "lat", "lon", "arrived_at"}
