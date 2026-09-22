"""Staff sign-in: tokens, the dependency on every admin route, and failing closed."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import admin, auth
from app.config import get_settings

PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_password", SecretStr(PASSWORD))
    monkeypatch.setattr(auth, "FAILED_LOGIN_DELAY_S", 0)
    app = FastAPI()
    app.include_router(auth.router)

    @app.get("/api/admin/probe", dependencies=[Depends(auth.require_admin)])
    async def probe() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_token_round_trip():
    token, expires = auth.issue_token(PASSWORD, 3600, now=1_000)
    assert expires == 4_600
    assert auth.token_is_valid(token, PASSWORD, now=2_000)


def test_token_expires():
    token, _ = auth.issue_token(PASSWORD, 60, now=1_000)
    assert not auth.token_is_valid(token, PASSWORD, now=1_061)


def test_token_is_bound_to_the_password():
    token, _ = auth.issue_token(PASSWORD, 3600)
    assert not auth.token_is_valid(token, "a new password")


@pytest.mark.parametrize("token", ["", "abc", "123.", ".deadbeef", "notdigits.deadbeef"])
def test_malformed_tokens_are_rejected(token):
    assert not auth.token_is_valid(token, PASSWORD)


def test_tampered_expiry_is_rejected():
    token, expires = auth.issue_token(PASSWORD, 60, now=1_000)
    forged = f"{expires + 10_000}.{token.split('.', 1)[1]}"
    assert not auth.token_is_valid(forged, PASSWORD, now=1_000)


def test_login_then_access(client):
    response = client.post("/api/admin/login", json={"password": PASSWORD})
    assert response.status_code == 200
    token = response.json()["token"]
    assert client.get("/api/admin/probe", headers={"Authorization": f"Bearer {token}"}).json() == {"ok": True}


def test_wrong_password(client):
    assert client.post("/api/admin/login", json={"password": "guess"}).status_code == 401


def test_no_token_is_rejected(client):
    response = client.get("/api/admin/probe")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_unconfigured_password_fails_closed(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_password", None)
    assert client.get("/api/admin/probe").status_code == 503
    assert client.post("/api/admin/login", json={"password": "x"}).status_code == 503


def test_every_admin_route_requires_sign_in():
    dependencies = [d.dependency for d in admin.router.dependencies]
    assert auth.require_admin in dependencies
    assert {r.path for r in admin.router.routes} >= {"/api/admin/summary", "/api/admin/insights"}
