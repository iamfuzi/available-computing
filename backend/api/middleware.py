"""HTTP middleware for the proxy.

Currently provides request-id propagation so every proxy response carries an
``X-AC-Request-ID`` header that callers can use to correlate a request with
server-side logs and the ``X-AC-Attempted-Models`` routing trace.

The id is generated when absent (``ac_req_<uuid_hex>``) and echoed on every
response, including errors. Handlers can read the resolved id from
``request.state.ac_request_id``.
"""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-AC-Request-ID"


def generate_request_id() -> str:
    return f"ac_req_{uuid.uuid4().hex}"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Ensure every response carries a stable ``X-AC-Request-ID``.

    If the caller supplies one (same header name), it is reused so that a
    client-side trace id propagates end-to-end. Otherwise a fresh id is
    minted. The value is stored on ``request.state.ac_request_id`` for
    downstream handlers and error builders.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or generate_request_id()
        request.state.ac_request_id = request_id
        response: Response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        # 变更公告机器可读通道：所有响应带 X-AC-Notice（有活跃公告时），
        # 像 GitHub 的 deprecation 头。调用方 SDK/网关可无成本捕获。
        if request.url.path.startswith("/v1/") or request.url.path.startswith("/api/v1/auth/public"):
            try:
                from services.notices_center import header_notice
                notice = header_notice()
                if notice:
                    # HTTP 头仅允许 latin-1：中文标题需 URL 编码；id/级别
                    # 为 ASCII 可直放。调用方拿 id 去公开端点拉详情。
                    import urllib.parse
                    response.headers["X-AC-Notice"] = str(notice.get("id", ""))
                    response.headers["X-AC-Notice-Level"] = str(notice.get("level", "info"))
                    response.headers["X-AC-Notice-Title"] = urllib.parse.quote(
                        str(notice.get("title", "")), safe="")
                    if notice.get("action_required"):
                        response.headers["X-AC-Notice-Action-Required"] = "true"
            except Exception:
                pass
        return response


def get_request_id(request: Request) -> str:
    """Read the request id set by the middleware, falling back to a fresh one.

    The fallback only triggers if a response is built outside the middleware
    chain (e.g. in a unit test); production paths always have it set.
    """
    return getattr(request.state, "ac_request_id", None) or generate_request_id()
