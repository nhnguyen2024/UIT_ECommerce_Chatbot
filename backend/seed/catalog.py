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

    A jacket is "Ao khoac du" whoever makes it, so every brand shares one list.
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
        slug="mens-fashion",
        subcategory_slug="jackets",
        category_vi="Thời trang nam",
        category_en="Men's fashion",
        subcategory_vi="Áo khoác",
        subcategory_en="Jackets",
        code="MJK",
        brand_lines=shared_lines(
            ["Routine", "Yame", "Coolmate", "Owen", "Aristino"],
            [("Áo khoác dù", "Windbreaker Jacket"), ("Áo khoác bomber", "Bomber Jacket"),
             ("Áo khoác jean", "Denim Jacket"), ("Áo hoodie", "Hoodie")],
        ),
        variants=[("", ""), ("form rộng", "oversized"), ("2 lớp", "double-layer"),
                  ("có mũ", "hooded")],
        price_range=(199_000, 1_290_000),
        attributes={
            "material": translated("Chất liệu", "Material",
                                   ("Vải dù", "Parachute fabric"),
                                   ("Kaki cotton", "Cotton khaki"),
                                   ("Nỉ bông", "Fleece"),
                                   ("Jean cotton", "Cotton denim")),
            "sizes": same("Kích cỡ", "Sizes", "S, M, L, XL", "M, L, XL, XXL"),
            "colours": translated("Màu sắc", "Colours",
                                  ("Đen, Xám, Navy", "Black, Grey, Navy"),
                                  ("Đen, Be, Rêu", "Black, Beige, Olive"),
                                  ("Trắng, Đen", "White, Black")),
            "fit": translated("Kiểu dáng", "Fit",
                              ("Form regular", "Regular fit"),
                              ("Form rộng", "Relaxed fit"),
                              ("Form ôm", "Slim fit")),
            "season": translated("Mùa", "Season",
                                 ("Thu đông", "Autumn and winter"),
                                 ("Bốn mùa", "All seasons")),
        },
        blurb_vi=(
            "Áo khoác nam dáng trẻ trung, chất vải dày dặn giữ ấm tốt nhưng vẫn thoáng khi mặc "
            "trong nhà. Dễ phối cùng quần jean hoặc quần âu, mặc đi học đi làm và đi chơi đều hợp. "
            "Đường may chắc chắn, ít nhăn, giặt máy được."
        ),
        blurb_en=(
            "Men's jacket with a youthful cut and substantial fabric that keeps you warm outdoors "
            "yet stays breathable indoors. Pairs easily with jeans or chinos for class, work, or "
            "going out. Sturdy stitching, wrinkle resistant, machine washable."
        ),
        tags_vi=["áo khoác nam", "giữ ấm", "mùa đông", "thời trang nam"],
        tags_en=["men's jacket", "warm", "winter", "men's fashion"],
        warranty_months=0,
    ),
    CategoryTemplate(
        slug="womens-fashion",
        subcategory_slug="dresses",
        category_vi="Thời trang nữ",
        category_en="Women's fashion",
        subcategory_vi="Váy và đầm",
        subcategory_en="Dresses and skirts",
        code="WDR",
        brand_lines=shared_lines(
            ["Elise", "IVY moda", "NEM", "Vascara", "Hnoss"],
            [("Đầm suông", "Shift Dress"), ("Đầm xòe", "A-line Dress"),
             ("Chân váy midi", "Midi Skirt"), ("Đầm công sở", "Office Dress")],
        ),
        variants=[("", ""), ("tay dài", "long sleeve"), ("cổ vuông", "square neck"),
                  ("phối bèo", "ruffled")],
        price_range=(349_000, 2_490_000),
        attributes={
            "material": translated("Chất liệu", "Material",
                                   ("Lụa tằm", "Mulberry silk"),
                                   ("Voan", "Chiffon"),
                                   ("Cotton lạnh", "Cool cotton"),
                                   ("Tuyết mưa", "Crepe")),
            "sizes": same("Kích cỡ", "Sizes", "S, M, L", "S, M, L, XL"),
            "colours": translated("Màu sắc", "Colours",
                                  ("Đen, Trắng, Hồng", "Black, White, Pink"),
                                  ("Xanh navy, Be", "Navy, Beige"),
                                  ("Đỏ đô, Đen", "Burgundy, Black")),
            "occasion": translated("Dịp mặc", "Occasion",
                                   ("Công sở", "Office"),
                                   ("Dạo phố", "Casual outings"),
                                   ("Dự tiệc", "Parties")),
            "length": translated("Chiều dài", "Length",
                                 ("Trên gối", "Above the knee"),
                                 ("Ngang gối", "Knee length"),
                                 ("Midi", "Midi")),
        },
        blurb_vi=(
            "Đầm nữ thiết kế thanh lịch, chất vải mềm rũ không bí khi mặc cả ngày. Phom dáng tôn "
            "eo, che khuyết điểm bắp tay, phù hợp mặc đi làm và dự tiệc nhẹ. Có thể phối cùng "
            "blazer khi trời lạnh."
        ),
        blurb_en=(
            "Elegant women's dress in a soft, draping fabric that stays comfortable all day. The "
            "silhouette flatters the waist and softens the upper arms, suitable for the office and "
            "for informal events. Layer with a blazer in cooler weather."
        ),
        tags_vi=["đầm nữ", "công sở", "thời trang nữ", "dự tiệc"],
        tags_en=["women's dress", "office wear", "women's fashion", "party"],
        warranty_months=0,
    ),
    CategoryTemplate(
        slug="footwear",
        subcategory_slug="trainers",
        category_vi="Giày dép",
        category_en="Footwear",
        subcategory_vi="Giày thể thao",
        subcategory_en="Trainers",
        code="SHO",
        brand_lines={
            "Biti's": [("Hunter", "Hunter"), ("Hunter Street", "Hunter Street")],
            "Ananas": [("Basas", "Basas"), ("Urbas", "Urbas")],
            "Vento": [("Runner", "Runner"), ("Sandal", "Sandal")],
            "Converse": [("Chuck Taylor", "Chuck Taylor"), ("Run Star", "Run Star")],
        },
        variants=[("", ""), ("Low", "Low"), ("High", "High"), ("Lite", "Lite")],
        price_range=(320_000, 3_490_000),
        attributes={
            "material": translated("Chất liệu", "Material",
                                   ("Vải canvas", "Canvas"),
                                   ("Da tổng hợp", "Synthetic leather"),
                                   ("Lưới thoáng khí", "Breathable mesh")),
            "sizes": same("Kích cỡ", "Sizes", "36-43", "38-45", "35-40"),
            "sole": translated("Đế giày", "Sole",
                               ("Đế cao su", "Rubber sole"),
                               ("Đế phylon", "Phylon sole"),
                               ("Đế IP", "IP sole")),
            "colours": translated("Màu sắc", "Colours",
                                  ("Trắng, Đen", "White, Black"),
                                  ("Đen, Xám, Trắng", "Black, Grey, White"),
                                  ("Be, Xanh", "Beige, Blue")),
            "cut": translated("Kiểu", "Cut",
                              ("Cổ thấp", "Low top"),
                              ("Cổ cao", "High top")),
        },
        blurb_vi=(
            "Giày thể thao nhẹ, đế êm, đi bộ nhiều không mỏi chân. Phần thân lưới thoáng khí giảm "
            "hầm bí khi trời nóng. Dễ phối đồ, dùng được cả khi đi học, đi làm và tập luyện nhẹ."
        ),
        blurb_en=(
            "Lightweight trainers with a cushioned sole that stay comfortable over long walks. The "
            "breathable mesh upper reduces heat build-up. Easy to style for class, work, or light "
            "training."
        ),
        tags_vi=["giày thể thao", "đi bộ", "thoáng khí"],
        tags_en=["trainers", "sneakers", "walking", "breathable"],
        warranty_months=0,
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
        slug="watches",
        subcategory_slug="wristwatches",
        category_vi="Đồng hồ",
        category_en="Watches",
        subcategory_vi="Đồng hồ đeo tay",
        subcategory_en="Wristwatches",
        code="WAT",
        brand_lines={
            "Casio": [("MTP", "MTP"), ("LTP", "LTP")],
            "Citizen": [("Eco-Drive", "Eco-Drive"), ("Quartz", "Quartz")],
            "Orient": [("Bambino", "Bambino"), ("Mako", "Mako")],
            "Daniel Wellington": [("Petite", "Petite"), ("Classic", "Classic")],
            "Curnon": [("Kepler", "Kepler"), ("Aurora", "Aurora")],
        },
        variants=[("", ""), ("Nam", "Men"), ("Nữ", "Women"), ("Automatic", "Automatic")],
        price_range=(890_000, 9_990_000),
        attributes={
            "movement": translated("Loại máy", "Movement",
                                   ("Quartz pin", "Battery quartz"),
                                   ("Automatic cơ", "Mechanical automatic"),
                                   ("Năng lượng ánh sáng", "Solar powered")),
            "case_size": same("Đường kính mặt", "Case diameter", "32mm", "36mm", "40mm", "42mm"),
            "strap": translated("Chất liệu dây", "Strap material",
                                ("Dây da", "Leather strap"),
                                ("Dây thép không gỉ", "Stainless steel bracelet"),
                                ("Dây vải", "Fabric strap")),
            "water_resistance": same("Chống nước", "Water resistance", "3ATM", "5ATM", "10ATM"),
            "crystal": translated("Kính", "Crystal",
                                  ("Kính khoáng", "Mineral crystal"),
                                  ("Kính sapphire", "Sapphire crystal")),
        },
        blurb_vi=(
            "Đồng hồ thiết kế tối giản, mặt số dễ đọc, phù hợp đeo đi học và đi làm. Dây tháo lắp "
            "nhanh nên đổi phong cách dễ dàng. Khả năng chống nước đủ dùng khi rửa tay và đi mưa "
            "nhẹ, không dùng khi bơi lặn."
        ),
        blurb_en=(
            "Minimalist watch with a legible dial, suitable for class and the office. The "
            "quick-release strap makes it easy to change style. Water resistance covers hand "
            "washing and light rain, but not swimming or diving."
        ),
        tags_vi=["đồng hồ", "tối giản", "quà tặng"],
        tags_en=["watch", "minimalist", "gift"],
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
