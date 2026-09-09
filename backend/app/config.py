"""Application settings, loaded from environment variables or a .env file."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Anthropic ---------------------------------------------------------
    # The SDK reads ANTHROPIC_API_KEY from the environment on its own; we do not
    # pass it explicitly, so it is not duplicated here.
    agent_model: str = "claude-opus-5"
    classifier_model: str = "claude-haiku-4-5"

    # Effort controls how much the agent thinks per turn. "high" is the default
    # and the right starting point; drop to "medium" if latency becomes a problem.
    agent_effort: str = "high"

    # Hard ceiling on tool-calling rounds within a single user turn. Without this
    # a confused model can loop until it exhausts the context window.
    max_tool_rounds: int = 6

    # --- MongoDB -----------------------------------------------------------
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "uit_ecommerce_chatbot"

    # Names of the Atlas Search indexes created by app/db/indexes.py.
    products_vector_index: str = "products_vector"
    products_text_index: str = "products_text"
    policies_vector_index: str = "policies_vector"

    # --- Embeddings --------------------------------------------------------
    # "auto"     Atlas Automated Embedding. Atlas calls Voyage AI itself, so this
    #            codebase never computes a vector. Public preview, and the default.
    # "explicit" We compute vectors and store them in an `embedding` field.
    #            The fallback if the preview API changes. See app/db/vector.py.
    embedding_mode: Literal["auto", "explicit"] = "auto"

    # voyage-4 is MongoDB's recommended general-purpose model. It is what the
    # index is built with, so changing it means rebuilding the index.
    voyage_model: str = "voyage-4"

    # Only read in "explicit" mode, where it must match the model's output size.
    embedding_dimensions: int = 1024

    # Only read in "explicit" mode.
    voyage_api_key: str | None = None

    # --- HTTP --------------------------------------------------------------
    cors_origins: list[str] = [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ]

    # --- Behaviour ---------------------------------------------------------
    # Order lookups must match a contact detail. This is the number of trailing
    # phone digits a customer has to supply. See app/agent/tools/orders.py.
    order_verify_digits: int = 4

    # Pricing per million tokens, used only to record an estimated cost per turn
    # in telemetry. Update if Anthropic pricing changes.
    price_input_per_mtok: float = 5.00
    price_output_per_mtok: float = 25.00
    price_cache_read_per_mtok: float = 0.50


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so settings are parsed once per process."""
    return Settings()
