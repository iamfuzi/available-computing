"""路由加固回归测试：400 换道、槽位排队、类别候选降级、per-key 用量"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import Channel, Model
from services.crypto import encrypt


def _mk_response(status_code: int, payload: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    resp.headers = {}
    resp.text = ""
    return resp


def _install_upstream(monkeypatch, handler):
    """把 httpx.AsyncClient.post 换成按 payload 分发的假上游。

    handler(payload_dict) -> MagicMock response（同步函数即可）。"""
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    async def _post(url, json=None, headers=None):
        return handler(json or {})

    mock_cm.post = AsyncMock(side_effect=_post)
    monkeypatch.setattr("httpx.AsyncClient", MagicMock(return_value=mock_cm))
    return mock_cm


def _add_channel(db_session, fixed_salt, channel_id: str, provider: str) -> Channel:
    channel = Channel(
        id=channel_id,
        provider_type=provider,
        name=channel_id,
        api_key_enc=encrypt("sk-x", "test-admin-password", fixed_salt),
        enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    return channel


def _add_model(db_session, channel, model_id: str, *, category="text", ms=100) -> Model:
    model = Model(
        id=f"mdl-{model_id}",
        channel_id=channel.id,
        model_id=model_id,
        display_name=model_id,
        category=category,
        is_free=True,
        free_type="permanent",
        free_source="whitelist",
        health_status="healthy",
        last_response_ms=ms,
        is_active=True,
    )
    db_session.add(model)
    db_session.commit()
    return model


# ── P0：上游 400 换道 ──────────────────────────────────────────────────────


class TestChat400Failover:
    @pytest.mark.asyncio
    async def test_400_on_first_candidate_falls_through_to_second(
        self, app_client, auth_headers, db_session, sample_channel, fixed_salt, monkeypatch
    ):
        ch2 = _add_channel(db_session, fixed_salt, "ch-b", "siliconflow")
        # 100ms 排在 800ms 之前：model-a 必然先被尝试
        _add_model(db_session, sample_channel, "failover-a", ms=100)
        _add_model(db_session, ch2, "failover-b", ms=800)

        def handler(payload):
            if payload.get("model") == "failover-a":
                return _mk_response(400, {"error": {"message": "max_tokens exceeds limit"}})
            return _mk_response(
                200,
                {"id": "chatcmpl-1", "choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            )

        _install_upstream(monkeypatch, handler)

        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-AC-Selected-Model") == "failover-b"
        attempted = resp.headers.get("X-AC-Attempted-Models", "")
        assert "failover-a" in attempted and "failover-b" in attempted

    @pytest.mark.asyncio
    async def test_all_candidates_reject_400_replays_first_rejection(
        self, app_client, auth_headers, sample_model, sample_channel, monkeypatch
    ):
        _install_upstream(
            monkeypatch,
            lambda payload: _mk_response(400, {"error": {"message": "bad"}}),
        )
        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "test-model-free", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        error = resp.json()["error"]
        assert error["code"] == "upstream_non_retryable_error"
        assert error.get("attempted_models") or error.get("attempted")


# ── P1：槽位排队 + 分类别限流 ─────────────────────────────────────────────


class TestSlotQueueing:
    def test_slot_limits_per_category(self):
        import api.proxy as proxy

        assert proxy._slot_limit("embedding") == proxy.PROXY_EMBEDDING_CONCURRENCY_LIMIT
        assert proxy._slot_limit("rerank") == proxy.PROXY_EMBEDDING_CONCURRENCY_LIMIT
        assert proxy._slot_limit("chat") == proxy.PROXY_MODEL_CONCURRENCY_LIMIT
        assert proxy._slot_limit("image") == proxy.PROXY_MODEL_CONCURRENCY_LIMIT

    @pytest.mark.asyncio
    async def test_waits_for_slot_instead_of_failing_fast(self, monkeypatch):
        import api.proxy as proxy

        monkeypatch.setattr(proxy, "PROXY_SLOT_QUEUE_TIMEOUT_SECONDS", 1.0)
        proxy._model_semaphores.clear()

        channel = Channel(id="q-ch", provider_type="openrouter", name="q", api_key_enc="x", enabled=True)
        model = Model(id="q-m", channel_id="q-ch", model_id="q", category="text")

        key, ok = await proxy._try_acquire_model_slot(channel, model)
        assert ok

        async def _release_later():
            await asyncio.sleep(0.05)
            proxy._release_model_slot(key)

        release_task = asyncio.create_task(_release_later())
        key2, ok2 = await proxy._try_acquire_model_slot(channel, model)
        assert ok2, "槽位被占时应排队等待释放，而不是立即 503"
        await release_task
        proxy._release_model_slot(key2)
        proxy._model_semaphores.clear()

    @pytest.mark.asyncio
    async def test_times_out_when_slot_never_frees(self, monkeypatch):
        import api.proxy as proxy

        monkeypatch.setattr(proxy, "PROXY_SLOT_QUEUE_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(proxy, "PROXY_MODEL_CONCURRENCY_LIMIT", 1)
        proxy._model_semaphores.clear()

        channel = Channel(id="t-ch", provider_type="openrouter", name="t", api_key_enc="x", enabled=True)
        model = Model(id="t-m", channel_id="t-ch", model_id="t", category="text")

        key, ok = await proxy._try_acquire_model_slot(channel, model)
        assert ok
        _, ok2 = await proxy._try_acquire_model_slot(channel, model)
        assert not ok2
        proxy._release_model_slot(key)
        proxy._model_semaphores.clear()


# ── P1：embeddings / rerank 同类别候选降级 ────────────────────────────────


class TestEmbeddingsFailover:
    @pytest.mark.asyncio
    async def test_upstream_500_falls_back_to_second_embedding(
        self, app_client, auth_headers, db_session, sample_channel, fixed_salt, monkeypatch
    ):
        ch2 = _add_channel(db_session, fixed_salt, "ch-emb-b", "siliconflow")
        _add_model(db_session, sample_channel, "bge-m3", category="embedding", ms=100)
        _add_model(db_session, ch2, "bge-large", category="embedding", ms=800)

        def handler(payload):
            if payload.get("model") == "bge-m3":
                return _mk_response(500, {"error": "upstream boom"})
            return _mk_response(200, {"object": "list", "data": [{"embedding": [0.1], "index": 0}]})

        _install_upstream(monkeypatch, handler)

        resp = await app_client.post(
            "/v1/embeddings",
            json={"model": "bge-m3", "input": ["文本"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-AC-Selected-Model") == "bge-large"
        attempted = resp.headers.get("X-AC-Attempted-Models", "")
        assert "bge-m3" in attempted and "bge-large" in attempted

    @pytest.mark.asyncio
    async def test_payload_switches_to_fallback_model_id(
        self, app_client, auth_headers, db_session, sample_channel, fixed_salt, monkeypatch
    ):
        ch2 = _add_channel(db_session, fixed_salt, "ch-emb-c", "siliconflow")
        _add_model(db_session, sample_channel, "bge-m3", category="embedding", ms=100)
        _add_model(db_session, ch2, "bge-large", category="embedding", ms=800)

        seen_models = []

        def handler(payload):
            seen_models.append(payload.get("model"))
            if payload.get("model") == "bge-m3":
                return _mk_response(500, {"error": "boom"})
            return _mk_response(200, {"data": [{"embedding": [0.2], "index": 0}]})

        _install_upstream(monkeypatch, handler)

        resp = await app_client.post(
            "/v1/embeddings",
            json={"model": "bge-m3", "input": ["文本"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert seen_models == ["bge-m3", "bge-large"]


# ── P2：per-key 用量 ──────────────────────────────────────────────────────


class TestKeyUsage:
    @pytest.mark.asyncio
    async def test_record_flush_and_query(self, app_client, auth_headers):
        from services import usage

        usage._counters.clear()
        usage.record_usage("key-1", "embeddings", "success")
        usage.record_usage("key-1", "embeddings", "success")
        usage.record_usage("key-1", "embeddings", "success")
        usage.record_usage("key-1", "chat", "fail")
        usage.record_usage("admin", "chat", "success")

        written = usage.flush_usage()
        assert written == 3

        resp = await app_client.get("/api/v1/apikeys/usage?days=1", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        totals = {t["api_key_id"]: t for t in body["totals"]}
        assert totals["key-1"]["success"] == 3
        assert totals["key-1"]["fail"] == 1
        assert totals["admin"]["success"] == 1
        assert body["pending_flush"] == 0

        # 再记一次并幂等累加
        usage.record_usage("key-1", "embeddings", "success")
        usage.flush_usage()
        resp = await app_client.get("/api/v1/apikeys/usage?days=1", headers=auth_headers)
        totals = {t["api_key_id"]: t for t in resp.json()["totals"]}
        assert totals["key-1"]["success"] == 4
        usage._counters.clear()


# ── RPM 防护：默认模型限额 + 供应商聚合限额 ──────────────────────────────


class TestRPMThrottle:
    def _seed_passive(self, db_session, model, count=2):
        from models import HealthRecord

        for _ in range(count):
            db_session.add(
                HealthRecord(
                    model_id=model.id,
                    status="ok",
                    is_passive=True,
                    response_ms=100,
                )
            )
        db_session.commit()

    def test_default_model_rpm_floor_for_headerless_providers(
        self, db_session, sample_model, monkeypatch
    ):
        """无 rate-limit 头的供应商（智谱/讯飞）也能吃到默认 RPM 兜底。"""
        import api.proxy as proxy

        monkeypatch.setattr(proxy, "PROXY_DEFAULT_MODEL_RPM", 2)
        assert not sample_model.rate_limit
        self._seed_passive(db_session, sample_model, count=2)

        with pytest.raises(proxy.ModelBudgetExceeded) as exc:
            proxy._check_model_budget(sample_model, db_session)
        assert exc.value.reason == "local_rpm_exceeded"

    def test_observed_rpm_still_takes_priority(
        self, db_session, sample_model, sample_channel, monkeypatch
    ):
        """模型自带 observed rpm 时不被默认值覆盖（这里 observed 更宽松）。"""
        import json as _json

        import api.proxy as proxy

        monkeypatch.setattr(proxy, "PROXY_DEFAULT_MODEL_RPM", 1)
        sample_model.rate_limit = _json.dumps({"rpm": 10})
        db_session.add(sample_model)
        db_session.commit()
        self._seed_passive(db_session, sample_model, count=2)

        proxy._check_model_budget(sample_model, db_session)  # 不应抛出

    def test_provider_rpm_aggregates_all_models_on_channel(
        self, db_session, sample_model, sample_channel, monkeypatch
    ):
        """同一 channel 的模型共享供应商 RPM 窗口。"""
        import api.proxy as proxy

        monkeypatch.setattr(proxy, "PROXY_PROVIDER_RPM", 2)
        other = Model(
            id="mdl-sibling",
            channel_id=sample_channel.id,
            model_id="sibling-model",
            display_name="sibling",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=100,
        )
        db_session.add(other)
        db_session.commit()

        # 配额全部消耗在 sibling 上，本模型自身 0 次也应被拦
        self._seed_passive(db_session, other, count=2)

        with pytest.raises(proxy.ModelBudgetExceeded) as exc:
            proxy._check_model_budget(sample_model, db_session)
        assert exc.value.reason == "local_provider_rpm_exceeded"
