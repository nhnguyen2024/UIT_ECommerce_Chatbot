"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

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

@app.exception_handler(PyMongoError)
async def handle_database_error(request: Request, exc: PyMongoError) -> JSONResponse:
    """Report a datastore outage as 503 rather than 500.

    Registered once here instead of wrapped around each endpoint. A 500 tells a
    client the service is broken and there is no point retrying; a 503 says the
    service is fine and its datastore is not, which is both true and actionable.
    The distinction also keeps the dashboard's error state honest: it can say the
    database is unreachable rather than blaming itself.

    The exception detail is logged but never returned. A PyMongo error message
    carries the connection string's host and topology, which should not reach a
    browser.
    """
    logger.warning("database error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "The database is unavailable. Please try again shortly."},
    )


app.include_router(health.router)
app.include_router(chat.router)
app.include_router(admin.router)
