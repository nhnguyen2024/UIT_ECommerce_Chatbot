"""Admin dashboard endpoints.

Everything here reads the `events` collection, one row per user turn, using
aggregation pipelines. The dashboard therefore never scans conversation
transcripts, which keeps it fast as history grows.

These endpoints are unauthenticated in this build. Before this is exposed
anywhere public, put authentication in front of the router: the summary reveals
operating cost and the review queue quotes shopper messages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from app.db import schema
from app.db.client import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@router.get("/summary")
async def summary(days: int = Query(7, ge=1, le=90)) -> dict:
    """Headline numbers for the selected window."""
    pipeline = [
        {"$match": {"at": {"$gte": _since(days)}}},
        {
            "$group": {
                "_id": None,
                "turns": {"$sum": 1},
                "sessions": {"$addToSet": "$session_id"},
                "escalations": {"$sum": {"$cond": ["$escalated", 1, 0]}},
                "blocked": {"$sum": {"$cond": ["$blocked", 1, 0]}},
                "ungrounded": {"$sum": {"$cond": ["$grounded", 0, 1]}},
                "errors": {"$sum": {"$cond": [{"$ifNull": ["$error", False]}, 1, 0]}},
                "cost_usd": {"$sum": "$cost_usd"},
                "avg_latency_ms": {"$avg": "$latency_ms"},
                "input_tokens": {"$sum": "$input_tokens"},
                "output_tokens": {"$sum": "$output_tokens"},
                "cache_read_tokens": {"$sum": "$cache_read_tokens"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "turns": 1,
                "sessions": {"$size": "$sessions"},
                "escalations": 1,
                "blocked": 1,
                "ungrounded": 1,
                "errors": 1,
                "cost_usd": {"$round": ["$cost_usd", 4]},
                "avg_latency_ms": {"$round": ["$avg_latency_ms", 0]},
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_tokens": 1,
            }
        },
    ]
    rows = await (await get_db()[schema.EVENTS].aggregate(pipeline)).to_list(1)
    if not rows:
        return {
            "days": days,
            "turns": 0,
            "sessions": 0,
            "escalations": 0,
            "blocked": 0,
            "ungrounded": 0,
            "errors": 0,
            "cost_usd": 0.0,
            "avg_latency_ms": 0,
            "cache_hit_rate": 0.0,
            "cost_per_turn_usd": 0.0,
        }

    row = rows[0]
    row["days"] = days

    # Share of input tokens served from cache. A rate near zero means something
    # volatile crept into the prompt prefix and caching has silently stopped.
    billable_input = row.get("input_tokens", 0) + row.get("cache_read_tokens", 0)
    row["cache_hit_rate"] = (
        round(row.get("cache_read_tokens", 0) / billable_input, 3) if billable_input else 0.0
    )
    row["cost_per_turn_usd"] = (
        round(row["cost_usd"] / row["turns"], 5) if row.get("turns") else 0.0
    )
    return row


@router.get("/intents")
async def intents(days: int = Query(7, ge=1, le=90)) -> list[dict]:
    """What shoppers are asking about, most common first."""
    pipeline = [
        {"$match": {"at": {"$gte": _since(days)}}},
        {
            "$group": {
                "_id": "$intent",
                "turns": {"$sum": 1},
                "escalations": {"$sum": {"$cond": ["$escalated", 1, 0]}},
                "avg_latency_ms": {"$avg": "$latency_ms"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "intent": "$_id",
                "turns": 1,
                "escalations": 1,
                "avg_latency_ms": {"$round": ["$avg_latency_ms", 0]},
            }
        },
        {"$sort": {"turns": -1}},
    ]
    return await (await get_db()[schema.EVENTS].aggregate(pipeline)).to_list(20)


@router.get("/timeseries")
async def timeseries(days: int = Query(14, ge=1, le=90)) -> list[dict]:
    """Daily volume, cost, and escalations."""
    pipeline = [
        {"$match": {"at": {"$gte": _since(days)}}},
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$at"}},
                "turns": {"$sum": 1},
                "escalations": {"$sum": {"$cond": ["$escalated", 1, 0]}},
                "cost_usd": {"$sum": "$cost_usd"},
                "avg_latency_ms": {"$avg": "$latency_ms"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$_id",
                "turns": 1,
                "escalations": 1,
                "cost_usd": {"$round": ["$cost_usd", 4]},
                "avg_latency_ms": {"$round": ["$avg_latency_ms", 0]},
            }
        },
        {"$sort": {"date": 1}},
    ]
    return await (await get_db()[schema.EVENTS].aggregate(pipeline)).to_list(90)


@router.get("/tools")
async def tool_usage(days: int = Query(7, ge=1, le=90)) -> list[dict]:
    """How often each tool is called. Reveals dead tools and over-used ones."""
    pipeline = [
        {"$match": {"at": {"$gte": _since(days)}}},
        {"$unwind": "$tools_used"},
        {"$group": {"_id": "$tools_used", "calls": {"$sum": 1}}},
        {"$project": {"_id": 0, "tool": "$_id", "calls": 1}},
        {"$sort": {"calls": -1}},
    ]
    return await (await get_db()[schema.EVENTS].aggregate(pipeline)).to_list(20)


@router.get("/handoffs")
async def handoffs(status: str = Query("open"), limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    """The human agent's queue."""
    return (
        await get_db()[schema.HANDOFFS]
        .find({"status": status}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(limit)
    )


@router.get("/review-queue")
async def review_queue(days: int = Query(7, ge=1, le=90), limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    """Turns worth a human read: ungrounded answers and errors.

    This is the quality feedback loop. An answer flagged ungrounded stated a
    figure that appeared in no tool result, which is the failure mode most worth
    catching in a system that quotes prices and deadlines.
    """
    return (
        await get_db()[schema.EVENTS]
        .find(
            {
                "at": {"$gte": _since(days)},
                "$or": [{"grounded": False}, {"error": {"$ne": None}}],
            },
            {"_id": 0},
        )
        .sort("at", -1)
        .to_list(limit)
    )


@router.get("/insights")
async def insights() -> dict:
    """The latest analytics results written back from Snowflake's gold layer.

    analytics/pipeline.py (step "feedback") inserts one document per run; the
    newest one is returned. An empty response means the pipeline has not run
    yet, which the page shows as such rather than as an error.
    """
    document = await get_db()[schema.INSIGHTS].find_one({}, {"_id": 0}, sort=[("generated_at", -1)])
    return document or {}
