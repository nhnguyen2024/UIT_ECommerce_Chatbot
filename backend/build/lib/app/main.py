"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, chat, health
from app.config import get_settings
from app.db.client import close_client, ping

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Connect eagerly so a bad connection string surfaces in the startup logs
    # rather than as a failed shopper request several minutes later. It is a
    # warning, not a fatal error: the health endpoint should still answer so an
    # operator can see why the container is unhealthy.
    try:
        await ping()
        logger.info("connected to MongoDB database %r", settings.mongodb_db)
    except Exception:
        logger.warning("could not reach MongoDB at startup; /ready will report it", exc_info=True)

    logger.info(
        "agent=%s classifier=%s embedding_mode=%s",
        settings.agent_model,
        settings.classifier_model,
        settings.embedding_mode,
    )

    yield

    await close_client()


app = FastAPI(
    title="E-Commerce Support Chatbot",
    description=(
        "Product consultation, policy questions, and order tracking, in "
        "Vietnamese and English."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(admin.router)
