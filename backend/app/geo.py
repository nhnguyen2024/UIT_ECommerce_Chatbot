"""Named places an order passes through, and where they are.

Carrier tracking events name places ("arrived at the Đà Nẵng sorting hub"),
never coordinates, so a real deployment keeps exactly this kind of table and
looks each event's place up in it. Orders store the key; coordinates stay here,
so the map can be redrawn or corrected without touching order data.

Destinations are provinces, not addresses. The map needs nothing finer, and a
street address has no business in a chat window that anyone holding an order
code and a phone number can open.

Province names follow the 2025 reorganisation into 34 provincial units. Each
point is the provincial seat, approximately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PlaceKind = Literal["warehouse", "hub", "destination"]
Region = Literal["north", "central", "south"]


@dataclass(frozen=True)
class Place:
    key: str
    name_vi: str
    name_en: str
    lat: float
    lon: float
    kind: PlaceKind
    region: Region

    def name(self, lang: str) -> str:
        return self.name_vi if lang == "vi" else self.name_en


_PLACES = [
    # Where orders are packed. Both fulfil every channel.
    Place("wh-hanoi", "Kho Hà Nội (Long Biên)", "Hanoi warehouse (Long Biên)", 21.047, 105.886, "warehouse", "north"),
    Place("wh-hcm", "Kho TP.HCM (Thủ Đức)", "HCMC warehouse (Thủ Đức)", 10.850, 106.772, "warehouse", "south"),
    # Regional sorting hubs, one per region.
    Place("hub-north", "Trung tâm phân loại Hà Nội", "Hanoi sorting hub", 20.975, 105.785, "hub", "north"),
    Place("hub-central", "Trung tâm phân loại Đà Nẵng", "Đà Nẵng sorting hub", 16.030, 108.170, "hub", "central"),
    Place("hub-south", "Trung tâm phân loại TP.HCM", "HCMC sorting hub", 10.760, 106.620, "hub", "south"),
    # Delivery provinces.
    Place("hanoi", "Hà Nội", "Hanoi", 21.028, 105.854, "destination", "north"),
    Place("haiphong", "Hải Phòng", "Hai Phong", 20.845, 106.688, "destination", "north"),
    Place("quangninh", "Quảng Ninh", "Quang Ninh", 20.959, 107.042, "destination", "north"),
    Place("bacninh", "Bắc Ninh", "Bac Ninh", 21.186, 106.076, "destination", "north"),
    Place("thainguyen", "Thái Nguyên", "Thai Nguyen", 21.594, 105.848, "destination", "north"),
    Place("laocai", "Lào Cai", "Lao Cai", 22.486, 103.970, "destination", "north"),
    Place("ninhbinh", "Ninh Bình", "Ninh Binh", 20.251, 105.975, "destination", "north"),
    Place("thanhhoa", "Thanh Hóa", "Thanh Hoa", 19.807, 105.776, "destination", "north"),
    Place("nghean", "Nghệ An", "Nghe An", 18.679, 105.681, "destination", "north"),
    Place("hue", "Huế", "Hue", 16.463, 107.590, "destination", "central"),
    Place("danang", "Đà Nẵng", "Da Nang", 16.054, 108.202, "destination", "central"),
    Place("quangngai", "Quảng Ngãi", "Quang Ngai", 15.120, 108.792, "destination", "central"),
    Place("khanhhoa", "Khánh Hòa", "Khanh Hoa", 12.238, 109.196, "destination", "south"),
    Place("daklak", "Đắk Lắk", "Dak Lak", 12.667, 108.038, "destination", "south"),
    Place("lamdong", "Lâm Đồng", "Lam Dong", 11.940, 108.458, "destination", "south"),
    Place("hcm", "TP. Hồ Chí Minh", "Ho Chi Minh City", 10.776, 106.700, "destination", "south"),
    Place("dongnai", "Đồng Nai", "Dong Nai", 10.957, 106.843, "destination", "south"),
    Place("cantho", "Cần Thơ", "Can Tho", 10.045, 105.747, "destination", "south"),
    Place("vinhlong", "Vĩnh Long", "Vinh Long", 10.254, 105.972, "destination", "south"),
    Place("camau", "Cà Mau", "Ca Mau", 9.177, 105.150, "destination", "south"),
]

PLACES: dict[str, Place] = {place.key: place for place in _PLACES}
DESTINATIONS: list[str] = [place.key for place in _PLACES if place.kind == "destination"]
HUB_FOR_REGION: dict[Region, str] = {"north": "hub-north", "central": "hub-central", "south": "hub-south"}


def plan_route(warehouse: str, destination: str) -> list[str]:
    """The places a parcel passes, from the warehouse to the destination province.

    Warehouse, then its region's sorting hub, then Đà Nẵng when the parcel
    crosses between north and south, then the destination region's hub, then
    the destination. Repeated hubs collapse, so a Hanoi order sent from the
    Hanoi warehouse goes warehouse, Hanoi hub, Hanoi.
    """
    origin_region = PLACES[warehouse].region
    target_region = PLACES[destination].region
    stops = [warehouse, HUB_FOR_REGION[origin_region]]
    if {origin_region, target_region} == {"north", "south"}:
        stops.append(HUB_FOR_REGION["central"])
    stops.append(HUB_FOR_REGION[target_region])
    stops.append(destination)
    route: list[str] = []
    for stop in stops:
        if not route or route[-1] != stop:
            route.append(stop)
    return route


# Shipping zones from the published shipping policy (shipping-policy.md,
# "Shipping fees"). Checkout charges from this table, so the chatbot's quoted
# fees and the fee a shopper actually pays come from the same rules.
INNER_CITY = {"hanoi", "hcm"}
MOUNTAINOUS = {"laocai"}
FREE_SHIPPING_FROM = 500_000


def shipping_fee(destination: str, subtotal: int) -> int:
    if subtotal >= FREE_SHIPPING_FROM:
        return 0
    if destination in INNER_CITY:
        return 25_000
    if destination in MOUNTAINOUS:
        return 55_000
    return 35_000


def nearest_warehouse(destination: str) -> str:
    """The warehouse that ships to a province: Hanoi for the north, HCMC otherwise."""
    return "wh-hanoi" if PLACES[destination].region == "north" else "wh-hcm"
