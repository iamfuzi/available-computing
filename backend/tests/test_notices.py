import pytest
from unittest.mock import patch

from services import notices_center


@pytest.fixture(autouse=True)
def _reset_cache():
    notices_center._cache = None
    yield
    notices_center._cache = None


class TestNoticesCenter:
    def test_create_list_delete_roundtrip(self, db_session):
        notice = notices_center.create_notice(
            title="行为变更", body="说明", level="warning",
            action_required=True, session=db_session,
        )
        active = notices_center.active_notices(use_cache=False)
        assert any(n["id"] == notice["id"] and n["action_required"] for n in active)
        assert notices_center.delete_notice(notice["id"], session=db_session)
        assert all(n["id"] != notice["id"] for n in notices_center.active_notices(use_cache=False))

    def test_expired_notice_hidden(self, db_session):
        notice = notices_center.create_notice(
            title="过期测试", body="", level="info",
            expires_at="2000-01-01T00:00:00Z", session=db_session,
        )
        assert all(n["id"] != notice["id"] for n in notices_center.active_notices(use_cache=False))

    def test_invalid_level_rejected(self, db_session):
        with pytest.raises(ValueError):
            notices_center.create_notice(title="x", body="", level="critical", session=db_session)

    def test_header_notice_prefers_action_required(self, db_session):
        notices_center.create_notice(title="普通", body="", session=db_session)
        urgent = notices_center.create_notice(
            title="必须适配", body="", level="breaking", action_required=True, session=db_session,
        )
        assert notices_center.header_notice()["id"] == urgent["id"]


class TestNoticesApi:
    @pytest.mark.asyncio
    async def test_public_endpoint_no_auth(self, app_client):
        resp = await app_client.get("/api/v1/auth/public/notices")
        assert resp.status_code == 200
        assert "notices" in resp.json()

    @pytest.mark.asyncio
    async def test_admin_create_and_header_appears(self, app_client, auth_headers, db_session):
        resp = await app_client.post("/api/v1/notices", headers=auth_headers, json={
            "title": "auto 路由行为变更", "body": "空正文自动换道", "level": "warning", "action_required": True,
        })
        assert resp.status_code == 201
        notices_center._cache = None
        # /v1/* 响应头带公告（HTTP 头 latin-1 限制：id/级别直放，标题 URL 编码）
        r = await app_client.get("/v1/models", headers=auth_headers)
        assert r.headers.get("X-AC-Notice", "").startswith("n")
        assert r.headers.get("X-AC-Notice-Level") == "warning"
        assert "%E8%" in r.headers.get("X-AC-Notice-Title", "") or r.headers.get("X-AC-Notice-Title")
        assert r.headers.get("X-AC-Notice-Action-Required") == "true"

    @pytest.mark.asyncio
    async def test_self_test_carries_notices(self, app_client, auth_headers, sample_model, sample_channel, db_session):
        notices_center.create_notice(
            title="自检可见", body="", level="info", session=db_session,
        )
        notices_center._cache = None
        resp = await app_client.post("/v1/ac/self-test", headers=auth_headers, json={})
        assert resp.status_code == 200
        assert any(n["title"] == "自检可见" for n in resp.json().get("notices", []))
