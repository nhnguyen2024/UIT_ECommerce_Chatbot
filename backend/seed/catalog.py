"""Synthetic product catalogue.

The catalogue is generated from hand-written per-category templates rather than
random word salad. Retrieval quality is the thing being demonstrated, and it
cannot be judged against products whose descriptions carry no meaning: a query
for "tai nghe chong on" has to match a noise-cancelling headphone description on
substance, not on a coincidence of random tokens.

Generation is seeded, so re-running produces identical output. The evaluation
dataset references specific SKUs, and those references have to survive a re-seed.

All identifiers are English. Product names, descriptions, and specification
labels are stored in both Vietnamese and English, because serving both languages
is the feature under test.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.db.schema import Product, ProductAttribute

SEED = 20260909
TARGET_COUNT = 480


@dataclass(frozen=True)
class AttributeSpec:
    """Possible values for one specification row, with bilingual labels."""

    label_vi: str
    label_en: str
    values: list[tuple[str, str]]  # (Vietnamese, English)


def same(label_vi: str, label_en: str, *values: str) -> AttributeSpec:
    """Attribute whose values read identically in both languages.

    Specifications such as "128GB", "1200W", or "Bluetooth 5.3" are not
    translated, so both language fields carry the same string.
    """
    return AttributeSpec(label_vi, label_en, [(value, value) for value in values])


def translated(label_vi: str, label_en: str, *pairs: tuple[str, str]) -> AttributeSpec:
    """Attribute whose values differ between languages."""
    return AttributeSpec(label_vi, label_en, list(pairs))


@dataclass
class CategoryTemplate:
    slug: str  # English filter key, e.g. "phones"
    subcategory_slug: str
    category_vi: str
    category_en: str
    subcategory_vi: str
    subcategory_en: str
    code: str  # ASCII SKU prefix; category names are not ASCII.
    # Model lines belong to a brand. Pairing them at random produces nonsense
    # like "vivo Redmi Note", which is exactly the kind of detail a reader of the
    # report would notice, so each brand carries its own lines.
    brand_lines: dict[str, list[tuple[str, str]]]
    variants: list[tuple[str, str]]  # suffix appended to the model name
    price_range: tuple[int, int]
    attributes: dict[str, AttributeSpec]  # English key -> spec
    blurb_vi: str
    blurb_en: str
    tags_vi: list[str] = field(default_factory=list)
    tags_en: list[str] = field(default_factory=list)
    warranty_months: int = 12


def shared_lines(
    brands: list[str], lines: list[tuple[str, str]]
) -> dict[str, list[tuple[str, str]]]:
    """For categories where the product name is generic rather than a brand line.

    An air fryer is "Noi chien khong dau" whoever makes it, so every brand in
    that category shares one list of product names. Categories where the model
    line belongs to the brand, such as phones or laptops, must not use this:
    pairing them at random produces nonsense like "vivo Redmi Note".
    """
    return {brand: lines for brand in brands}


TEMPLATES: list[CategoryTemplate] = [
    CategoryTemplate(
        slug="phones",
        subcategory_slug="smartphones",
        category_vi="Điện thoại",
        category_en="Phones",
        subcategory_vi="Điện thoại thông minh",
        subcategory_en="Smartphones",
        code="PHN",
        brand_lines={
            "Samsung": [("Galaxy A", "Galaxy A"), ("Galaxy M", "Galaxy M")],
            "Xiaomi": [("Redmi Note", "Redmi Note"), ("POCO X", "POCO X")],
            "OPPO": [("Reno", "Reno"), ("A", "A")],
            "vivo": [("V", "V"), ("Y", "Y")],
            "realme": [("realme C", "realme C"), ("realme Note", "realme Note")],
            "TECNO": [("Spark", "Spark"), ("Camon", "Camon")],
        },
        variants=[("", ""), ("Pro", "Pro"), ("Pro+", "Pro+"), ("5G", "5G"), ("Lite", "Lite")],
        price_range=(2_990_000, 24_990_000),
        attributes={
            "ram": same("RAM", "RAM", "4GB", "6GB", "8GB", "12GB"),
            "storage": same("Bộ nhớ trong", "Internal storage", "64GB", "128GB", "256GB", "512GB"),
            "display": same("Màn hình", "Display",
                            '6.4" AMOLED 90Hz', '6.7" AMOLED 120Hz', '6.6" IPS 90Hz'),
            "battery": same("Pin", "Battery", "4500 mAh", "5000 mAh", "6000 mAh"),
            "rear_camera": same("Camera sau", "Rear camera",
                                "50MP + 8MP", "64MP + 8MP + 2MP", "108MP + 8MP + 2MP"),
        },
        blurb_vi=(
            "Điện thoại thông minh màn hình lớn, pin dung lượng cao dùng thoải mái cả ngày. "
            "Camera chụp đêm rõ nét, sạc nhanh, phù hợp cho học sinh sinh viên và người dùng "
            "phổ thông cần một máy bền và mượt trong tầm giá."
        ),
        blurb_en=(
            "Large-screen smartphone with a high-capacity battery that comfortably lasts a full "
            "day. Sharp low-light camera and fast charging, suited to students and everyday users "
            "who want a durable, smooth phone in this price bracket."
        ),
        tags_vi=["điện thoại", "pin trâu", "sạc nhanh", "chụp ảnh"],
        tags_en=["smartphone", "long battery life", "fast charging", "photography"],
    ),
    CategoryTemplate(
        slug="laptops",
        subcategory_slug="study-laptops",
        category_vi="Laptop",
        category_en="Laptops",
        subcategory_vi="Laptop văn phòng và học tập",
        subcategory_en="Office and study laptops",
        code="LAP",
        brand_lines={
            "ASUS": [("VivoBook", "VivoBook"), ("Zenbook", "Zenbook")],
            "Acer": [("Aspire", "Aspire"), ("Swift", "Swift")],
            "Lenovo": [("IdeaPad", "IdeaPad"), ("ThinkBook", "ThinkBook")],
            "HP": [("Pavilion", "Pavilion"), ("ProBook", "ProBook")],
            "Dell": [("Inspiron", "Inspiron"), ("Vostro", "Vostro")],
            "MSI": [("Modern", "Modern"), ("Katana", "Katana")],
        },
        variants=[("14", "14"), ("15", "15"), ("16", "16"),
                  ("14 OLED", "14 OLED"), ("15 Gaming", "15 Gaming")],
        price_range=(11_990_000, 42_990_000),
        attributes={
            "cpu": same("CPU", "CPU", "Intel Core i3", "Intel Core i5", "Intel Core i7",
                        "AMD Ryzen 5", "AMD Ryzen 7"),
            "ram": same("RAM", "RAM", "8GB", "16GB", "32GB"),
            "storage": same("Ổ cứng", "Storage", "256GB SSD", "512GB SSD", "1TB SSD"),
            "graphics": translated("Card đồ họa", "Graphics",
                                   ("Card tích hợp", "Integrated graphics"),
                                   ("NVIDIA RTX 3050", "NVIDIA RTX 3050"),
                                   ("NVIDIA RTX 4050", "NVIDIA RTX 4050")),
            "weight": same("Khối lượng", "Weight", "1.4 kg", "1.7 kg", "2.1 kg"),
        },
        blurb_vi=(
            "Laptop mỏng nhẹ cho công việc văn phòng, lập trình và học tập. Bàn phím hành trình "
            "tốt, màn hình chống chói, thời lượng pin đủ dùng cho một buổi học. Máy chạy mượt các "
            "tác vụ đa nhiệm, soạn thảo, họp trực tuyến và lập trình cơ bản."
        ),
        blurb_en=(
            "Thin and light laptop for office work, programming, and study. Comfortable keyboard "
            "travel, anti-glare display, and enough battery for a full class session. Handles "
            "multitasking, document work, video calls, and everyday development smoothly."
        ),
        tags_vi=["laptop", "sinh viên", "văn phòng", "lập trình", "mỏng nhẹ"],
        tags_en=["laptop", "students", "office", "programming", "lightweight"],
    ),
    CategoryTemplate(
        slug="audio",
        subcategory_slug="headphones",
        category_vi="Âm thanh",
        category_en="Audio",
        subcategory_vi="Tai nghe",
        subcategory_en="Headphones",
        code="AUD",
        brand_lines={
            "Sony": [("WH-CH", "WH-CH"), ("WF-C", "WF-C")],
            "JBL": [("Tune", "Tune"), ("Wave", "Wave")],
            "Anker": [("Soundcore Life", "Soundcore Life"), ("Soundcore Space", "Soundcore Space")],
            "Baseus": [("Encok", "Encok"), ("Bowie", "Bowie")],
            "Havit": [("TW", "TW"), ("H", "H")],
            "Edifier": [("W", "W"), ("X", "X")],
        },
        variants=[("", ""), ("Pro", "Pro"), ("ANC", "ANC"), ("Air", "Air"), ("Plus", "Plus")],
        price_range=(390_000, 8_990_000),
        attributes={
            "form_factor": translated("Kiểu dáng", "Form factor",
                                      ("True Wireless", "True wireless"),
                                      ("Chụp tai", "Over-ear"),
                                      ("Nhét tai có dây", "Wired in-ear")),
            "noise_cancelling": translated("Chống ồn", "Noise cancelling",
                                           ("Chống ồn chủ động ANC", "Active noise cancelling"),
                                           ("Chống ồn thụ động", "Passive isolation"),
                                           ("Không có", "None")),
            "battery_life": translated("Thời lượng pin", "Battery life",
                                       ("6 giờ", "6 hours"), ("20 giờ", "20 hours"),
                                       ("30 giờ", "30 hours"), ("40 giờ", "40 hours")),
            "connectivity": same("Kết nối", "Connectivity",
                                 "Bluetooth 5.0", "Bluetooth 5.3", "Jack 3.5mm"),
            "water_resistance": translated("Kháng nước", "Water resistance",
                                           ("IPX4", "IPX4"), ("IPX5", "IPX5"),
                                           ("Không", "None")),
        },
        blurb_vi=(
            "Tai nghe cho nhu cầu nghe nhạc hằng ngày và học trực tuyến. Âm bass chắc, mic đàm "
            "thoại rõ, đeo êm tai khi dùng lâu. Bản có chống ồn chủ động giúp tập trung tốt hơn "
            "khi học bài trong quán cà phê hoặc di chuyển trên xe buýt."
        ),
        blurb_en=(
            "Headphones for daily listening and online classes. Solid bass, a clear call "
            "microphone, and comfortable long-session fit. The active noise cancelling variant "
            "helps you concentrate while studying in a cafe or commuting by bus."
        ),
        tags_vi=["tai nghe", "chống ồn", "bluetooth", "nghe nhạc", "học online"],
        tags_en=["headphones", "noise cancelling", "bluetooth", "music", "online study"],
        warranty_months=6,
    ),
    CategoryTemplate(
        slug="tablets",
        subcategory_slug="tablets",
        category_vi="Máy tính bảng",
        category_en="Tablets",
        subcategory_vi="Máy tính bảng",
        subcategory_en="Tablets",
        code="TAB",
        brand_lines={
            "Samsung": [("Galaxy Tab A", "Galaxy Tab A"), ("Galaxy Tab S", "Galaxy Tab S")],
            "Xiaomi": [("Pad", "Pad"), ("Redmi Pad", "Redmi Pad")],
            "Lenovo": [("Tab M", "Tab M"), ("Tab P", "Tab P")],
            "TCL": [("Tab", "Tab"), ("NxtPaper", "NxtPaper")],
            "Nokia": [("T", "T")],
        },
        variants=[("", ""), ("Plus", "Plus"), ("Lite", "Lite"), ("5G", "5G")],
        price_range=(3_290_000, 21_990_000),
        attributes={
            "screen": same("Màn hình", "Display",
                           '10.1" LCD', '10.9" LCD 90Hz', '11" AMOLED 120Hz', '12.4" AMOLED'),
            "storage": same("Bộ nhớ trong", "Storage", "64GB", "128GB", "256GB", "512GB"),
            "ram": same("RAM", "RAM", "4GB", "6GB", "8GB", "12GB"),
            "battery": same("Pin", "Battery", "7040 mAh", "8000 mAh", "10090 mAh"),
            "stylus": translated("Bút cảm ứng", "Stylus",
                                 ("Kèm bút", "Included"),
                                 ("Hỗ trợ, mua rời", "Supported, sold separately"),
                                 ("Không hỗ trợ", "Not supported")),
        },
        blurb_vi=(
            "Máy tính bảng màn hình lớn cho việc học trực tuyến, ghi chú và xem phim. Loa kép "
            "nghe rõ khi họp nhóm, pin đủ dùng qua vài tiết học liên tiếp. Bản có hỗ trợ bút phù "
            "hợp cho sinh viên hay vẽ hoặc ghi chép tay trực tiếp lên bài giảng."
        ),
        blurb_en=(
            "Large-screen tablet for online classes, note taking, and watching films. Stereo "
            "speakers stay clear in a group call, and the battery lasts several lessons back to "
            "back. The stylus-capable models suit students who sketch or annotate lecture slides."
        ),
        tags_vi=["máy tính bảng", "học online", "ghi chú", "giải trí", "pin lâu"],
        tags_en=["tablet", "online study", "note taking", "entertainment", "long battery"],
        warranty_months=12,
    ),
    CategoryTemplate(
        slug="televisions",
        subcategory_slug="smart-tvs",
        category_vi="Tivi",
        category_en="Televisions",
        subcategory_vi="Tivi thông minh",
        subcategory_en="Smart TVs",
        code="TVS",
        brand_lines={
            "Samsung": [("Crystal UHD", "Crystal UHD"), ("QLED", "QLED")],
            "LG": [("UHD", "UHD"), ("NanoCell", "NanoCell")],
            "Sony": [("Bravia", "Bravia")],
            "TCL": [("P", "P"), ("C", "C")],
            "Casper": [("Smart TV", "Smart TV")],
            "Coocaa": [("S", "S")],
        },
        variants=[("43 inch", "43 inch"), ("50 inch", "50 inch"),
                  ("55 inch", "55 inch"), ("65 inch", "65 inch")],
        price_range=(5_490_000, 32_990_000),
        attributes={
            "resolution": same("Độ phân giải", "Resolution", "Full HD", "4K UHD", "8K"),
            "panel": translated("Tấm nền", "Panel type",
                                ("LED viền", "Edge-lit LED"),
                                ("QLED chấm lượng tử", "QLED"),
                                ("OLED", "OLED")),
            "refresh_rate": same("Tần số quét", "Refresh rate", "50Hz", "60Hz", "120Hz"),
            "os": same("Hệ điều hành", "Operating system",
                       "Google TV", "Tizen", "webOS", "Android TV"),
            "hdmi_ports": same("Cổng HDMI", "HDMI ports", "2", "3", "4"),
        },
        blurb_vi=(
            "Tivi thông minh cho phòng khách gia đình, cài sẵn các ứng dụng xem phim và điều khiển "
            "bằng giọng nói tiếng Việt. Góc nhìn rộng nên cả nhà ngồi hai bên vẫn thấy rõ. Có cổng "
            "HDMI để cắm máy chơi game hoặc loa thanh."
        ),
        blurb_en=(
            "Smart television for a family living room, with streaming apps preinstalled and "
            "Vietnamese voice control. Wide viewing angles keep the picture clear for people "
            "sitting off to the side. HDMI ports for a games console or a soundbar."
        ),
        tags_vi=["tivi", "smart tv", "gia đình", "xem phim", "4k"],
        tags_en=["television", "smart tv", "family", "streaming", "4k"],
        warranty_months=24,
    ),
    CategoryTemplate(
        slug="accessories",
        subcategory_slug="power-and-charging",
        category_vi="Phụ kiện",
        category_en="Accessories",
        subcategory_vi="Nguồn và sạc",
        subcategory_en="Power and charging",
        code="ACC",
        # Deliberately the cheapest category. Without it every product costs
        # millions of dong and a shopper on a small budget has nothing to be
        # recommended, which makes the price filter impossible to demonstrate.
        brand_lines={
            "Anker": [("PowerCore", "PowerCore"), ("Nano", "Nano")],
            "Baseus": [("Bipow", "Bipow"), ("GaN", "GaN")],
            "Ugreen": [("Nexode", "Nexode"), ("Sạc nhanh", "Fast Charger")],
            "Xiaomi": [("Mi Power Bank", "Mi Power Bank")],
            "Belkin": [("BoostCharge", "BoostCharge")],
        },
        variants=[("", ""), ("Pro", "Pro"), ("Mini", "Mini"), ("Duo", "Duo")],
        price_range=(129_000, 1_890_000),
        attributes={
            "product_type": translated("Loại phụ kiện", "Accessory type",
                                       ("Pin dự phòng", "Power bank"),
                                       ("Củ sạc nhanh", "Fast charger"),
                                       ("Cáp sạc", "Charging cable"),
                                       ("Sạc không dây", "Wireless charger")),
            "capacity": same("Dung lượng", "Capacity",
                             "10000 mAh", "20000 mAh", "Không áp dụng"),
            "output": same("Công suất", "Output", "20W", "30W", "45W", "65W", "100W"),
            "ports": same("Cổng kết nối", "Ports",
                          "USB-C", "USB-C + USB-A", "2x USB-C + USB-A"),
            "warranty_note": translated("Bảo hành", "Warranty",
                                        ("12 tháng", "12 months"),
                                        ("18 tháng", "18 months")),
        },
        blurb_vi=(
            "Phụ kiện sạc dùng hằng ngày, nhỏ gọn bỏ vừa túi xách hoặc balo đi học. Có mạch bảo "
            "vệ chống quá nhiệt và quá dòng, sạc được cả điện thoại lẫn tai nghe. Phù hợp mang "
            "theo khi đi học cả ngày hoặc đi công tác ngắn."
        ),
        blurb_en=(
            "Everyday charging accessory, compact enough for a handbag or a school backpack. "
            "Protection circuitry guards against overheating and overcurrent, and it charges "
            "phones and earbuds alike. Suited to a full day out or a short business trip."
        ),
        tags_vi=["phụ kiện", "pin dự phòng", "sạc nhanh", "giá rẻ", "gọn nhẹ"],
        tags_en=["accessory", "power bank", "fast charging", "affordable", "compact"],
        warranty_months=12,
    ),
    CategoryTemplate(
        slug="home-appliances",
        subcategory_slug="kitchen-appliances",
        category_vi="Đồ gia dụng",
        category_en="Home appliances",
        subcategory_vi="Thiết bị nhà bếp",
        subcategory_en="Kitchen appliances",
        code="KIT",
        brand_lines=shared_lines(
            ["Sunhouse", "Kangaroo", "Lock&Lock", "Philips", "Sharp", "Bluestone"],
            [("Nồi chiên không dầu", "Air Fryer"), ("Máy xay sinh tố", "Blender"),
             ("Ấm siêu tốc", "Electric Kettle"), ("Nồi cơm điện", "Rice Cooker")],
        ),
        variants=[("", ""), ("dung tích lớn", "large capacity"),
                  ("cơ", "manual"), ("điện tử", "digital")],
        price_range=(290_000, 5_990_000),
        attributes={
            "power": same("Công suất", "Power", "800W", "1200W", "1500W", "1800W"),
            "capacity": translated("Dung tích", "Capacity",
                                   ("1.2 lít", "1.2 litres"), ("1.8 lít", "1.8 litres"),
                                   ("4.5 lít", "4.5 litres"), ("6 lít", "6 litres")),
            "inner_material": translated("Chất liệu lòng", "Inner material",
                                         ("Thép không gỉ", "Stainless steel"),
                                         ("Chống dính", "Non-stick"),
                                         ("Thủy tinh", "Glass")),
            "controls": translated("Điều khiển", "Controls",
                                   ("Núm vặn cơ", "Mechanical dial"),
                                   ("Cảm ứng điện tử", "Digital touch")),
            "origin": translated("Xuất xứ", "Origin",
                                 ("Việt Nam", "Vietnam"),
                                 ("Trung Quốc", "China"),
                                 ("Thái Lan", "Thailand")),
        },
        blurb_vi=(
            "Thiết bị nhà bếp dùng hằng ngày cho gia đình 2 đến 4 người. Vận hành êm, dễ vệ sinh, "
            "có chế độ tự ngắt khi quá nhiệt. Thiết kế gọn, đặt vừa các căn bếp nhỏ và phòng trọ "
            "sinh viên."
        ),
        blurb_en=(
            "Everyday kitchen appliance sized for a household of two to four. Runs quietly, cleans "
            "easily, and cuts power automatically on overheating. The compact body fits small "
            "kitchens and student accommodation."
        ),
        tags_vi=["gia dụng", "nhà bếp", "gia đình", "tiện lợi"],
        tags_en=["home appliance", "kitchen", "family", "convenient"],
    ),
    CategoryTemplate(
        slug="wearables",
        subcategory_slug="smartwatches",
        category_vi="Thiết bị đeo",
        category_en="Wearables",
        subcategory_vi="Đồng hồ thông minh",
        subcategory_en="Smartwatches",
        code="WAT",
        brand_lines={
            "Samsung": [("Galaxy Watch", "Galaxy Watch"), ("Galaxy Fit", "Galaxy Fit")],
            "Xiaomi": [("Smart Band", "Smart Band"), ("Watch S", "Watch S")],
            "Huawei": [("Watch GT", "Watch GT"), ("Band", "Band")],
            "Garmin": [("Forerunner", "Forerunner"), ("Venu", "Venu")],
            "Amazfit": [("GTS", "GTS"), ("Bip", "Bip")],
        },
        variants=[("", ""), ("Pro", "Pro"), ("Active", "Active"), ("Lite", "Lite")],
        price_range=(690_000, 12_990_000),
        attributes={
            "display": same("Màn hình", "Display",
                            '1.47" AMOLED', '1.62" AMOLED', '1.43" AMOLED', '1.09" TFT'),
            "battery_life": translated("Thời lượng pin", "Battery life",
                                       ("3 ngày", "3 days"), ("7 ngày", "7 days"),
                                       ("14 ngày", "14 days")),
            "health_sensors": translated("Cảm biến sức khỏe", "Health sensors",
                                         ("Nhịp tim, SpO2", "Heart rate, SpO2"),
                                         ("Nhịp tim, SpO2, giấc ngủ", "Heart rate, SpO2, sleep"),
                                         ("Nhịp tim", "Heart rate")),
            "gps": translated("Định vị", "GPS",
                              ("GPS tích hợp", "Built-in GPS"),
                              ("Dùng GPS của điện thoại", "Uses phone GPS")),
            "water_resistance": same("Chống nước", "Water resistance", "5ATM", "3ATM", "IP68"),
        },
        blurb_vi=(
            "Đồng hồ thông minh theo dõi bước chân, nhịp tim và giấc ngủ, hiển thị thông báo từ "
            "điện thoại ngay trên cổ tay. Pin dùng nhiều ngày nên không phải sạc mỗi tối. Bản có "
            "GPS tích hợp phù hợp cho người chạy bộ không muốn mang theo điện thoại."
        ),
        blurb_en=(
            "Smartwatch tracking steps, heart rate, and sleep, with phone notifications on your "
            "wrist. The battery runs for days, so there is no nightly charge. Models with "
            "built-in GPS suit runners who would rather leave the phone at home."
        ),
        tags_vi=["đồng hồ thông minh", "sức khỏe", "thể thao", "theo dõi giấc ngủ", "chống nước"],
        tags_en=["smartwatch", "health", "fitness", "sleep tracking", "water resistant"],
        warranty_months=12,
    ),
]


def _round_price(value: int) -> int:
    """Vietnamese retail prices end in 000, and usually 9000."""
    return (value // 10_000) * 10_000 + 9_000


def generate_products(count: int = TARGET_COUNT) -> list[Product]:
    rng = random.Random(SEED)
    products: list[Product] = []
    per_template = count // len(TEMPLATES)

    for template in TEMPLATES:
        for item_index in range(per_template):
            # sorted() keeps brand selection deterministic; dict order would
            # otherwise depend on literal ordering and change on edits.
            brand = rng.choice(sorted(template.brand_lines))
            line_vi, line_en = rng.choice(template.brand_lines[brand])
            variant_vi, variant_en = rng.choice(template.variants)
            number = rng.choice([3, 5, 7, 9, 11, 12, 13, 20, 22, 30, 50, 100])

            name_vi = " ".join(p for p in [brand, line_vi, str(number), variant_vi] if p)
            name_en = " ".join(p for p in [brand, line_en, str(number), variant_en] if p)

            sku = f"{template.code}-{item_index:03d}"

            low, high = template.price_range
            price = _round_price(rng.randint(low, high))

            # About a quarter of the catalogue is discounted, which gives the
            # agent something real to reason about when a shopper asks for deals.
            sale_price = None
            if rng.random() < 0.25:
                sale_price = _round_price(int(price * rng.uniform(0.70, 0.92)))

            attributes: list[ProductAttribute] = []
            for key, spec in template.attributes.items():
                value_vi, value_en = rng.choice(spec.values)
                attributes.append(
                    ProductAttribute(
                        key=key,
                        label_vi=spec.label_vi,
                        label_en=spec.label_en,
                        value_vi=value_vi,
                        value_en=value_en,
                    )
                )

            # Ratings cluster high, as they do on real marketplaces, but not
            # uniformly: a filter on "above 4 stars" has to exclude something.
            rating = round(rng.triangular(3.2, 5.0, 4.6), 1)
            review_count = int(rng.triangular(5, 2400, 180))
            stock = rng.choice([0, 3, 12, 25, 60, 140, 300])

            specs_vi = ", ".join(f"{a.label_vi} {a.value_vi}" for a in attributes)
            specs_en = ", ".join(f"{a.label_en} {a.value_en}" for a in attributes)
            description_vi = f"{template.blurb_vi} Thông số nổi bật: {specs_vi}."
            description_en = f"{template.blurb_en} Key specifications: {specs_en}."

            tags_vi = list(template.tags_vi)
            tags_en = list(template.tags_en)
            if sale_price is not None:
                tags_vi.append("đang giảm giá")
                tags_en.append("on sale")
            if template.warranty_months:
                tags_vi.append(f"bảo hành {template.warranty_months} tháng")
                tags_en.append(f"{template.warranty_months} month warranty")

            # One field carrying everything worth searching on, in both
            # languages. This is what gets embedded.
            embedding_source = "\n".join(
                [
                    name_vi,
                    name_en,
                    f"{template.category_vi} / {template.subcategory_vi}",
                    f"{template.category_en} / {template.subcategory_en}",
                    f"Thương hiệu / Brand: {brand}",
                    description_vi,
                    description_en,
                    " ".join(tags_vi + tags_en),
                ]
            )

            products.append(
                Product(
                    sku=sku,
                    name_vi=name_vi,
                    name_en=name_en,
                    category=template.slug,
                    category_vi=template.category_vi,
                    category_en=template.category_en,
                    subcategory=template.subcategory_slug,
                    subcategory_vi=template.subcategory_vi,
                    subcategory_en=template.subcategory_en,
                    brand=brand,
                    price=price,
                    sale_price=sale_price,
                    attributes=attributes,
                    description_vi=description_vi,
                    description_en=description_en,
                    embedding_source=embedding_source,
                    rating=rating,
                    review_count=review_count,
                    stock=stock,
                    images=[f"https://cdn.example.vn/products/{sku.lower()}.jpg"],
                    tags=tags_vi + tags_en,
                )
            )

    return products


if __name__ == "__main__":
    generated = generate_products()
    print(f"Generated {len(generated)} products\n")

    by_category: dict[str, int] = {}
    for product in generated:
        by_category[product.category] = by_category.get(product.category, 0) + 1
    for category, total in sorted(by_category.items()):
        print(f"  {category:<20} {total:>4}")

    print("\nSample product:")
    sample = generated[0]
    print(f"  SKU     {sample.sku}")
    print(f"  Name    {sample.name_vi}  /  {sample.name_en}")
    print(f"  Price   {sample.effective_price:,} VND".replace(",", "."))
    print("  Specs:")
    for attribute in sample.attributes:
        print(f"    {attribute.key:<18} {attribute.label_vi} = {attribute.value_vi}")
        print(f"    {'':<18} {attribute.label_en} = {attribute.value_en}")
