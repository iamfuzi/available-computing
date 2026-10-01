"""第三方变更公告中心。

设计：公告存在 Setting 表（key=third_party_notices），无需 migration；
管理 API 发布/删除，公开 API 与 self-test 拉取，全局响应头机器可读。
调用方是程序——通知必须送到它们已有的调用路径（self-test / 响应头），
而不是指望人去看页面。

生命周期：level=info/warning/breaking；expires_at 到期自动隐藏；
action_required=true 表示调用方必须评估适配。保留最近 20 条。
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlmodel import Session

from models import Setting


def _engine():
    # 惰性导入：测试通过替换 database.engine 注入内存库，顶层导入会在
    # 替换前绑定原 engine（services 层既有惯例）。
    from database import engine
    return engine

logger = logging.getLogger(__name__)

NOTICES_SETTING_KEY = "third_party_notices"
MAX_NOTICES = 20
NOTICE_CACHE_TTL_SECONDS = 60

_cache: tuple[float, list[dict]] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _is_active(notice: dict, now: datetime) -> bool:
    expires = notice.get("expires_at")
    if expires:
        try:
            if _parse_ts(expires) <= now:
                return False
        except ValueError:
            pass
    return True


def _load_all(session: Session) -> list[dict]:
    row = session.get(Setting, NOTICES_SETTING_KEY)
    if not row or not row.value:
        return []
    try:
        data = json.loads(row.value)
        return data if isinstance(data, list) else []
    except ValueError:
        logger.exception("third_party_notices setting malformed")
        return []


def _save_all(session: Session, notices: list[dict]) -> None:
    row = session.get(Setting, NOTICES_SETTING_KEY)
    trimmed = notices[:MAX_NOTICES]
    if row:
        row.value = json.dumps(trimmed, ensure_ascii=False)
    else:
        row = Setting(key=NOTICES_SETTING_KEY, value=json.dumps(trimmed, ensure_ascii=False))
    session.add(row)
    session.commit()


def active_notices(use_cache: bool = True) -> list[dict]:
    """当前有效公告（新→旧）。60s 进程内缓存：该函数挂在全局响应头上。"""
    global _cache
    import time as _time
    if use_cache and _cache and _cache[0] > _time.monotonic():
        return _cache[1]
    now = _now()
    with Session(_engine()) as session:
        notices = [n for n in _load_all(session) if _is_active(n, now)]
    _cache = (_time.monotonic() + NOTICE_CACHE_TTL_SECONDS, notices)
    return notices


def create_notice(
    title: str, body: str, level: str = "info",
    action_required: bool = False, expires_at: str | None = None,
    session: Session | None = None,
) -> dict:
    if level not in ("info", "warning", "breaking"):
        raise ValueError("level must be info/warning/breaking")
    notice = {
        "id": f"n{_now().strftime('%Y%m%d')}-{uuid4().hex[:6]}",
        "ts": _now().isoformat(),
        "level": level,
        "title": title,
        "body": body,
        "action_required": action_required,
    }
    if expires_at:
        notice["expires_at"] = expires_at
    own = session is None
    if own:
        session = Session(_engine())
    try:
        _save_all(session, [notice] + _load_all(session))
    finally:
        if own:
            session.close()
    global _cache
    _cache = None
    return notice


def delete_notice(notice_id: str, session: Session | None = None) -> bool:
    own = session is None
    if own:
        session = Session(_engine())
    try:
        remaining = [n for n in _load_all(session) if n.get("id") != notice_id]
        if len(remaining) == MAX_NOTICES and len(_load_all(session)) == len(remaining):
            return False
        _save_all(session, remaining)
        return True
    finally:
        global _cache
        _cache = None
        if own:
            session.close()


def header_notice() -> dict | None:
    """响应头携带的那一条：优先 breaking/warning 且 action_required，
    其次 7 天内的任意活跃公告。"""
    now = _now()
    notices = active_notices()
    for notice in notices:
        if notice.get("action_required") and notice.get("level") in ("breaking", "warning"):
            return notice
    for notice in notices:
        try:
            if now - _parse_ts(notice.get("ts", "")) < timedelta(days=7):
                return notice
        except (ValueError, TypeError):
            continue
    return None
