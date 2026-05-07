import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_success(self, app_client):
        resp = await app_client.post("/api/v1/auth/login", json={"password": "test-admin-password"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, app_client):
        resp = await app_client.post("/api/v1/auth/login", json={"password": "wrong"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_empty_password(self, app_client):
        resp = await app_client.post("/api/v1/auth/login", json={"password": ""})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_rate_limit(self, app_client):
        """10 failed attempts in 5 minutes → 429."""
        for i in range(10):
            await app_client.post("/api/v1/auth/login", json={"password": f"wrong-{i}"})
        resp = await app_client.post("/api/v1/auth/login", json={"password": "wrong-11"})
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_login_clears_rate_limit_on_success(self, app_client):
        """Successful login clears the rate limit counter for that IP."""
        for i in range(9):
            await app_client.post("/api/v1/auth/login", json={"password": f"wrong-{i}"})
        # 10th attempt succeeds
        resp = await app_client.post("/api/v1/auth/login", json={"password": "test-admin-password"})
        assert resp.status_code == 200
        # Should be able to try again (counter was cleared)
        resp = await app_client.post("/api/v1/auth/login", json={"password": "wrong"})
        assert resp.status_code == 401  # Not 429


class TestJWT:
    @pytest.mark.asyncio
    async def test_valid_token(self, auth_headers, app_client):
        resp = await app_client.get("/api/v1/channels", headers=auth_headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_token(self, app_client):
        resp = await app_client.get("/api/v1/channels")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_token(self, app_client):
        resp = await app_client.get("/api/v1/channels", headers={"Authorization": "Bearer invalid"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token(self, app_client):
        from jose import jwt
        from config import JWT_SECRET, JWT_ALGORITHM
        payload = {
            "sub": "admin",
            "iat": datetime.now(timezone.utc) - timedelta(days=30),
            "exp": datetime.now(timezone.utc) - timedelta(days=1),
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        resp = await app_client.get("/api/v1/channels", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401


class TestVerifyToken:
    def test_verify_token_returns_true(self):
        from api.auth import verify_token, create_token
        creds = MagicMock()
        creds.credentials = create_token()
        assert verify_token(creds) is True

    def test_verify_token_raises_on_invalid(self):
        from api.auth import verify_token
        from fastapi import HTTPException
        creds = MagicMock()
        creds.credentials = "garbage"
        with pytest.raises(HTTPException) as exc_info:
            verify_token(creds)
        assert exc_info.value.status_code == 401
