"""The model-provider switch: settings validation and client construction.

No request is sent. What is checked is that each provider produces a client
aimed at the right endpoint with the right credential, and that a Cortex
configuration missing a credential fails at startup rather than on a shopper's
first message.
"""

import pytest
from pydantic import ValidationError

from app.agent.client import build_client, cortex_base_url
from app.agent.tools import registry
from app.config import Settings

ACCOUNT = "https://abc-xy12345.snowflakecomputing.com"


def settings(**overrides) -> Settings:
    # _env_file=None keeps a developer's real backend/.env out of the test.
    return Settings(_env_file=None, **overrides)


class TestSettings:
    def test_anthropic_is_the_default(self):
        assert settings().llm_provider == "anthropic"

    def test_cortex_without_credentials_fails_at_startup(self):
        with pytest.raises(ValidationError, match="SNOWFLAKE_ACCOUNT_URL, SNOWFLAKE_PAT"):
            settings(llm_provider="cortex")

    def test_cortex_names_the_one_missing_setting(self):
        with pytest.raises(ValidationError, match="requires SNOWFLAKE_PAT"):
            settings(llm_provider="cortex", snowflake_account_url=ACCOUNT)

    def test_cortex_with_credentials_is_valid(self):
        configured = settings(
            llm_provider="cortex", snowflake_account_url=ACCOUNT, snowflake_pat="token"
        )
        assert configured.llm_provider == "cortex"

    def test_feature_switches_default_on(self):
        """On is correct for Anthropic; only a provider that rejects them turns them off."""
        configured = settings()
        assert configured.llm_strict_tools is True
        assert configured.llm_effort is True


class TestClient:
    def test_cortex_base_url_is_the_messages_root(self):
        """The SDK appends /v1/messages, which must land on Cortex's endpoint."""
        assert cortex_base_url(ACCOUNT) == f"{ACCOUNT}/api/v2/cortex"

    def test_trailing_slash_on_the_account_url_is_tolerated(self):
        assert cortex_base_url(ACCOUNT + "/") == f"{ACCOUNT}/api/v2/cortex"

    def test_cortex_client_targets_snowflake_with_a_bearer_token(self):
        client = build_client(
            settings(llm_provider="cortex", snowflake_account_url=ACCOUNT, snowflake_pat="secret")
        )
        assert str(client.base_url).rstrip("/") == f"{ACCOUNT}/api/v2/cortex"
        assert client.default_headers["Authorization"] == "Bearer secret"
        assert (
            client.default_headers["X-Snowflake-Authorization-Token-Type"]
            == "PROGRAMMATIC_ACCESS_TOKEN"
        )

    def test_anthropic_client_targets_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        client = build_client(settings())
        assert "api.anthropic.com" in str(client.base_url)
        assert "Authorization" not in client.default_headers

    def test_anthropic_key_from_settings_reaches_the_client(self, monkeypatch):
        """Regression: a key that exists only in backend/.env must reach the SDK.

        pydantic-settings loads .env into declared fields and never into
        os.environ, where the SDK looks. Before the key was a declared field, a
        local run with the key in .env had no key at all.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        client = build_client(settings(anthropic_api_key="sk-ant-from-dotenv"))
        assert client.api_key == "sk-ant-from-dotenv"

    def test_secrets_are_masked_in_reprs(self):
        """A settings object in a log line or traceback must not leak credentials."""
        configured = settings(
            llm_provider="cortex",
            snowflake_account_url=ACCOUNT,
            snowflake_pat="pat-value",
            anthropic_api_key="sk-ant-value",
        )
        assert "pat-value" not in repr(configured)
        assert "sk-ant-value" not in repr(configured)


class TestToolDefinitions:
    def test_strict_is_sent_by_default(self):
        assert all(tool["strict"] is True for tool in registry.definitions())

    def test_strict_can_be_switched_off(self):
        assert all("strict" not in tool for tool in registry.definitions(strict=False))

    def test_switching_strict_keeps_the_schema_and_the_order(self):
        """Order and schema are what the prompt cache and the model depend on."""
        on = registry.definitions()
        off = registry.definitions(strict=False)
        assert [t["name"] for t in on] == [t["name"] for t in off]
        for with_strict, without in zip(on, off):
            assert with_strict["input_schema"] == without["input_schema"]
            assert with_strict["input_schema"].get("additionalProperties") is False
