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


class TestContract:
    @pytest.mark.asyncio
    async def test_public_contract_no_auth(self, app_client):
        resp = await app_client.get("/api/v1/auth/public/contract")
        assert resp.status_code == 200
        body = resp.json()
        assert body["contract_version"] >= 1
        assert "all_candidates_empty_content" in body["error_codes"]
        assert body["error_codes"]["model_not_found"]["retryable"] is False
        assert body["error_codes"]["all_candidates_empty_content"]["recommended_action"] == "raise_budget"

    @pytest.mark.asyncio
    async def test_self_test_carries_contract_version(self, app_client, auth_headers, sample_model, sample_channel):
        resp = await app_client.post("/v1/ac/self-test", headers=auth_headers, json={})
        assert resp.status_code == 200
        assert resp.json().get("contract_version") >= 1

    def test_notice_with_change_type_and_affected(self, db_session):
        notice = notices_center.create_notice(
            title="新增错误码", body="说明", level="info",
            change_type="error_code_change", affected=["all_candidates_empty_content", "/v1/chat/completions"],
            session=db_session,
        )
        assert notice["change_type"] == "error_code_change"
        assert "all_candidates_empty_content" in notice["affected"]

    def test_notice_rejects_unknown_change_type(self, db_session):
        with pytest.raises(ValueError):
            notices_center.create_notice(title="x", body="", change_type="whatever", session=db_session)


class TestSpaFallbackAndPlaybook:
    @pytest.mark.asyncio
    async def test_integration_route_reachable_without_html_accept(self, app_client):
        # 程序探测（Accept: */*）也要能拿到 /integration（SPA 回退不再
        # 要求 text/html；hotspot 实报 404）
        r = await app_client.get("/integration", headers={"Accept": "*/*"})
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_api_404_stays_json_not_html(self, app_client):
        r = await app_client.get("/api/v1/nonexistent")
        assert r.status_code == 404
        assert "text/html" not in r.headers.get("content-type", "")

    def test_contract_contains_playbook(self):
        from services.contract import contract_payload, CHANGE_TYPES
        playbook = contract_payload()["playbook"]
        assert set(playbook.keys()) == set(CHANGE_TYPES)
        assert playbook["error_code_change"]["action"] == "fetch_error_codes_and_diff"
