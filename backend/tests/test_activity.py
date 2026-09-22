"""Simulated store activity: realistic, internally consistent, clearly marked, never in the future."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

import pytest

from app.db import schema
from seed.activity import build
from seed.orders import generate_orders

NOW = datetime(2026, 9, 22, 8, tzinfo=timezone.utc)
PRICES = (0.25, 2.0, 0.025)


@pytest.fixture(scope="module")
def docs():
    existing = [o.model_dump() for o in generate_orders()]
    return build(now=NOW, days=30, existing_orders=existing, prices=PRICES)


def test_deterministic():
    existing = [o.model_dump() for o in generate_orders()]
    a = build(now=NOW, days=5, existing_orders=existing, prices=PRICES)
    b = build(now=NOW, days=5, existing_orders=existing, prices=PRICES)
    assert [e["session_id"] for e in a[schema.EVENTS]] == [e["session_id"] for e in b[schema.EVENTS]]


def test_every_document_is_marked_simulated(docs):
    for name, rows in docs.items():
        assert rows, name
        assert all(row.get("is_simulated") is True for row in rows), name


def test_nothing_in_the_future_or_before_the_window(docs):
    start = NOW - timedelta(days=31)
    for event in docs[schema.EVENTS]:
        assert start <= event["at"] <= NOW
    for order in docs[schema.ORDERS]:
        assert order["created_at"] <= NOW and order["timeline"][-1]["at"] <= NOW


def test_events_validate_against_the_schema(docs):
    for event in docs[schema.EVENTS]:
        schema.TurnEvent.model_validate(event)


def test_orders_validate_and_have_unique_codes(docs):
    codes = [o["order_code"] for o in docs[schema.ORDERS]]
    assert len(codes) == len(set(codes))
    existing = {o.order_code for o in generate_orders()}
    assert not existing & set(codes)
    for order in docs[schema.ORDERS]:
        schema.Order.model_validate(order)
        assert order["channel"] == "website"


def test_telemetry_matches_what_the_deployed_system_measured(docs):
    events = [e for e in docs[schema.EVENTS] if e["tools_used"]]
    assert 9_000 < statistics.median(e["latency_ms"] for e in events) < 15_000
    assert 0.0015 < statistics.median(e["cost_usd"] for e in events) < 0.005
    share_vi = sum(e["lang"] == "vi" for e in docs[schema.EVENTS]) / len(docs[schema.EVENTS])
    assert 0.75 < share_vi < 0.95


def test_escalations_have_tickets_and_conversations_alternate(docs):
    escalated = {e["session_id"] for e in docs[schema.EVENTS] if e["escalated"]}
    assert escalated == {h["session_id"] for h in docs[schema.HANDOFFS]}
    for conv in docs[schema.CONVERSATIONS]:
        roles = [m["role"] for m in conv["messages"]]
        assert roles == ["user", "assistant"] * (len(roles) // 2)


def test_order_questions_only_ask_about_orders_that_already_existed(docs):
    placed = {o["order_code"]: o["created_at"] for o in docs[schema.ORDERS]}
    for conv in docs[schema.CONVERSATIONS]:
        for code, created in placed.items():
            if code in conv["messages"][0]["text"]:
                assert created < conv["created_at"]


def test_review_queue_and_blocked_turns_exist(docs):
    events = docs[schema.EVENTS]
    assert any(not e["grounded"] for e in events)
    assert any(e["blocked"] for e in events)
