"""The generated corpus: policy chunking, catalogue shape, and order integrity."""

import pytest

from app.db.schema import OrderStatus
from seed.catalog import TEMPLATES, generate_products
from seed.orders import SHOWCASE, STATUS_FLOW, generate_orders
from seed.parse_policies import parse_all


# --- Policies --------------------------------------------------------------


class TestPolicyChunks:
    chunks = parse_all()

    def test_every_document_produced_chunks(self):
        assert len({chunk.doc_id for chunk in self.chunks}) == 5

    def test_chunk_ids_are_unique(self):
        ids = [chunk.chunk_id for chunk in self.chunks]
        assert len(set(ids)) == len(ids)

    def test_chunk_ids_are_ascii_identifiers(self):
        """Citations are rendered in the UI and referenced by the eval set."""
        for chunk in self.chunks:
            assert chunk.chunk_id.isascii(), chunk.chunk_id
            assert "#" in chunk.chunk_id

    def test_both_languages_are_populated(self):
        for chunk in self.chunks:
            assert chunk.text_vi.strip()
            assert chunk.text_en.strip()

    def test_embedding_source_carries_both_languages(self):
        """A Vietnamese and an English question must reach the same chunk."""
        for chunk in self.chunks:
            assert chunk.text_vi in chunk.embedding_source
            assert chunk.text_en in chunk.embedding_source

    def test_source_url_points_at_the_section(self):
        for chunk in self.chunks:
            assert chunk.source_url.endswith(chunk.chunk_id.split("#", 1)[1])

    def test_parsing_is_deterministic(self):
        assert [c.chunk_id for c in parse_all()] == [c.chunk_id for c in self.chunks]


# --- Catalogue -------------------------------------------------------------


class TestCatalogue:
    products = generate_products()

    def test_generation_is_deterministic(self):
        """The eval dataset pins SKUs, so a re-seed must reproduce them."""
        again = generate_products()
        assert [p.sku for p in again] == [p.sku for p in self.products]
        assert [p.price for p in again] == [p.price for p in self.products]

    def test_skus_are_unique_and_ascii(self):
        skus = [p.sku for p in self.products]
        assert len(set(skus)) == len(skus)
        assert all(sku.isascii() for sku in skus)

    def test_category_slugs_are_ascii_filter_keys(self):
        """The search tool exposes these as an enum; they must be stable ASCII."""
        for product in self.products:
            assert product.category.isascii()
            assert product.subcategory.isascii()

    def test_category_slugs_match_the_tool_enum(self):
        from app.agent.tools.products import CATEGORIES

        assert {p.category for p in self.products} == set(CATEGORIES)

    def test_attribute_keys_are_english_identifiers(self):
        for product in self.products:
            for attribute in product.attributes:
                assert attribute.key.isascii()
                assert attribute.key.islower()

    def test_attributes_carry_both_languages(self):
        for product in self.products:
            for attribute in product.attributes:
                assert attribute.label_vi and attribute.label_en
                assert attribute.value_vi and attribute.value_en

    def test_brand_is_paired_with_its_own_model_line(self):
        """Guards against regenerating names like 'vivo Redmi Note'."""
        by_code = {template.code: template for template in TEMPLATES}
        for product in self.products:
            template = by_code[product.sku.split("-")[0]]
            own_lines = {line_vi for line_vi, _ in template.brand_lines[product.brand]}
            assert any(line in product.name_vi for line in own_lines), product.name_vi

    def test_sale_price_is_below_list_price(self):
        for product in self.products:
            if product.sale_price is not None:
                assert product.sale_price < product.price
                assert product.effective_price == product.sale_price

    def test_ratings_span_the_filter_boundary(self):
        """A 'four stars and above' filter has to actually exclude something."""
        ratings = [p.rating for p in self.products]
        assert any(r < 4.0 for r in ratings)
        assert any(r >= 4.0 for r in ratings)

    def test_some_products_are_out_of_stock(self):
        assert any(p.stock == 0 for p in self.products)

    def test_embedding_source_carries_both_languages(self):
        for product in self.products:
            assert product.description_vi in product.embedding_source
            assert product.description_en in product.embedding_source


# --- Orders ----------------------------------------------------------------


class TestOrders:
    orders = generate_orders()
    by_code = {order.order_code: order for order in orders}

    def test_order_codes_are_unique(self):
        codes = [order.order_code for order in self.orders]
        assert len(set(codes)) == len(codes)

    def test_timeline_ends_at_the_current_status(self):
        """An order must never claim a status its timeline does not reach."""
        for order in self.orders:
            assert order.timeline[-1].status == order.status

    def test_timeline_follows_the_declared_flow(self):
        for order in self.orders:
            assert [e.status for e in order.timeline] == STATUS_FLOW[order.status]

    def test_timeline_is_chronological(self):
        for order in self.orders:
            times = [entry.at for entry in order.timeline]
            assert times == sorted(times)

    def test_totals_add_up(self):
        for order in self.orders:
            expected = sum(item.unit_price * item.quantity for item in order.items)
            assert order.subtotal == expected
            assert order.total == order.subtotal + order.shipping_fee

    def test_free_shipping_threshold_matches_the_published_policy(self):
        """shipping-policy.md promises free delivery from 500,000 VND."""
        for order in self.orders:
            if order.subtotal >= 500_000:
                assert order.shipping_fee == 0

    def test_no_readable_contact_details_are_stored(self):
        """The database must hold hashes only."""
        for order in self.orders:
            dumped = order.model_dump()
            assert "phone" not in dumped
            assert "email" not in dumped
            assert len(order.phone_hash) == 64
            assert len(order.email_hash) == 64
            assert len(order.phone_last4) == 4

    @pytest.mark.parametrize("code,_phone,_email,status,_skus,_days", SHOWCASE)
    def test_showcase_orders_are_present_with_the_expected_status(
        self, code, _phone, _email, status, _skus, _days
    ):
        """The demo and the eval dataset reference these by code."""
        assert self.by_code[code].status == status

    def test_showcase_contacts_verify(self):
        from app.security import contact_matches

        for code, phone, email, *_ in SHOWCASE:
            order = self.by_code[code]
            assert contact_matches(
                supplied=phone, phone_hash=order.phone_hash, email_hash=order.email_hash
            )
            assert contact_matches(
                supplied=email, phone_hash=order.phone_hash, email_hash=order.email_hash
            )

    def test_in_transit_orders_have_a_carrier_and_tracking_code(self):
        for order in self.orders:
            if order.status in {"shipped", "out_for_delivery"}:
                assert order.carrier
                assert order.tracking_code
                assert order.estimated_delivery is not None

    def test_pending_orders_have_no_carrier(self):
        for order in self.orders:
            if order.status == "pending":
                assert order.carrier is None

    def test_every_status_in_the_enum_is_exercised(self):
        produced = {order.status for order in self.orders}
        assert produced == set(OrderStatus.__args__)
