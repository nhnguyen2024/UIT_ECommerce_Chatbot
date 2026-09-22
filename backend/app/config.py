"""Application settings, loaded from environment variables or a .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Model provider ----------------------------------------------------
    # Where Claude is called. Both speak the same Messages API, so the agent
    # loop, classifier and judge are identical either way; only the client
    # construction in app/agent/client.py differs.
    #
    # "anthropic"    Anthropic's API directly, with ANTHROPIC_API_KEY.
    # "cortex"       Snowflake Cortex, through its Anthropic-compatible Messages
    #                endpoint. Billed in Snowflake credits. Unavailable on trial
    #                accounts, which block the COMPLETE function.
    # "azure_openai" An Azure OpenAI deployment (OpenAI Chat Completions format),
    #                billed as Azure usage. AGENT_MODEL and CLASSIFIER_MODEL are
    #                then deployment names, e.g. "gpt-5-mini".
    #
    # The first two share a wire format; the third is translated in
    # app/agent/llm.py, which is the only module that knows the difference.
    llm_provider: Literal["anthropic", "cortex", "azure_openai"] = "anthropic"

    # Declared rather than left for the SDK to find in the environment. The SDK
    # reads os.environ, and pydantic-settings loads .env into these fields only,
    # never into os.environ, so an undeclared key in .env was silently invisible
    # to the SDK: local runs had no key, while Azure worked because it injects
    # real environment variables. SecretStr keeps the value out of reprs, logs
    # and tracebacks.
    anthropic_api_key: SecretStr | None = None

    # Only read when llm_provider is "cortex". The account URL looks like
    # https://<account-identifier>.snowflakecomputing.com, and the token is a
    # Snowflake programmatic access token (PAT).
    snowflake_account_url: str | None = None
    snowflake_pat: SecretStr | None = None

    # Only read when llm_provider is "azure_openai". The endpoint is the
    # resource's root, https://<resource>.openai.azure.com, with no path.
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None

    # Two request features that Cortex's documentation does not confirm. Both
    # default on, which is correct for Anthropic. Run `python -m app.agent.probe`
    # against a provider and switch off whichever it rejects.
    #
    # llm_strict_tools  `strict: true` on tool definitions, which guarantees the
    #                   model's arguments validate against each schema.
    # llm_effort        `output_config.effort` on the agent and judge calls.
    llm_strict_tools: bool = True
    llm_effort: bool = True

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

    # --- Staff pages -------------------------------------------------------
    # Password for the Operations and Insights pages (app/api/auth.py). Unset
    # means those pages stay locked (503), never open.
    admin_password: SecretStr | None = None
    admin_session_hours: int = 12

    # --- Behaviour ---------------------------------------------------------
    # Order lookups must match a contact detail. This is the number of trailing
    # phone digits a customer has to supply. See app/agent/tools/orders.py.
    order_verify_digits: int = 4

    # Pricing per million tokens, used only to record an estimated cost per turn
    # in telemetry. Update if Anthropic pricing changes. Under Cortex the real
    # bill is in Snowflake credits, so the figure becomes the Anthropic list-price
    # equivalent: still useful for comparing turns, not for reconciling a bill.
    price_input_per_mtok: float = 5.00
    price_output_per_mtok: float = 25.00
    price_cache_read_per_mtok: float = 0.50

    @model_validator(mode="after")
    def _provider_needs_credentials(self) -> "Settings":
        """Fail at startup, not on the first chat turn.

        A missing credential would otherwise surface as an opaque 401 or a
        connection error in front of a shopper, long after the misconfiguration.
        """
        required = {
            "cortex": (
                ("SNOWFLAKE_ACCOUNT_URL", self.snowflake_account_url),
                ("SNOWFLAKE_PAT", self.snowflake_pat),
            ),
            "azure_openai": (
                ("AZURE_OPENAI_ENDPOINT", self.azure_openai_endpoint),
                ("AZURE_OPENAI_API_KEY", self.azure_openai_api_key),
            ),
        }.get(self.llm_provider, ())
        missing = [name for name, value in required if not value]
        if missing:
            raise ValueError(f"LLM_PROVIDER={self.llm_provider} requires {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so settings are parsed once per process."""
    return Settings()
