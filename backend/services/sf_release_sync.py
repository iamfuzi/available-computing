"""Sync SiliconFlow release-notes to decommission officially retired models.

SiliconFlow's release-notes page (https://docs.siliconflow.cn/cn/release-notes/
overview) is SSR-rendered and publicly readable. Each retirement notice follows
a stable shape:

    ### 【模型服务调整】... 或 ### 平台服务调整通知
    ... 平台将于 YYYY-MM-DD 对下列模型进行下线处理：
    - org/model-id
    - org/model-id
    ...

We parse out the decommissioned model ids and deactivate any matching Model
rows, so the pool stops surfacing models that the upstream has retired (and
which would therefore fail every call).

This only handles explicit retirements. It does NOT discover new free models
(the release-notes record changes, not the full current catalog) — those still
rely on the static whitelist + manual curation.

Failure-safe: any fetch/parse error is logged and leaves existing data intact.
"""
import logging
import re

import httpx
from sqlmodel import Session, select

from config import SF_RELEASE_NOTES_URL, SF_RELEASE_SYNC_ENABLED
from database import engine
from models import Channel, Model

log = logging.getLogger("sf_release_sync")

# Match model ids like "org/model-name" or "Pro/org/model-name".
# Allows letters, digits, dots, dashes, underscores in each segment.
_MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9._-]+)+")

# A notice is relevant only if its body mentions retirement.
_RETIRE_KEYWORDS = ("下线", "将下线", "下线处理", "discontinued", "retire")


def _parse_decommissioned(text: str) -> set[str]:
    """Extract model ids that appear under a retirement notice.

    Walks the text block by block. A block becomes "active" when it contains a
    retirement keyword, and stays active through the following bullet list of
    model ids; it ends at the next heading (### ) or a non-id paragraph.
    """
    decommissioned: set[str] = set()
    lines = text.splitlines()
    in_retirement_block = False

    for line in lines:
        stripped = line.strip()
        # A new heading resets the block context.
        if stripped.startswith("#"):
            in_retirement_block = any(kw in stripped for kw in _RETIRE_KEYWORDS)
            continue

        # Detect retirement keyword in body text (some notices put it after the
        # heading, e.g. "平台将于 ... 对下列模型进行下线处理：").
        if any(kw in stripped for kw in _RETIRE_KEYWORDS):
            in_retirement_block = True

        if not in_retirement_block:
            continue

        # Only collect ids from bullet lines or backtick-quoted ids to avoid
        # grabbing stray org/path tokens from prose.
        is_bullet = stripped.startswith("-") or stripped.startswith("*")
        backtick_ids = re.findall(r"`([A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+)`", stripped)
        if backtick_ids:
            decommissioned.update(backtick_ids)
        elif is_bullet:
            for match in _MODEL_ID_RE.finditer(stripped):
                mid = match.group(0)
                # Filter out URLs and path-like noise (e.g. cloud.siliconflow.cn/models).
                if "/" not in mid or mid.endswith(("/models", "/api")) or "." in mid.split("/")[0]:
                    continue
                decommissioned.add(mid)

    return decommissioned


async def _fetch_text(url: str, timeout: float = 30.0) -> str:
    """Fetch the release-notes page as plain text."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "available-computing-sync/1.0"})
        r.raise_for_status()
        return r.text


async def sync_sf_decommissioned_models() -> dict:
    """Fetch release-notes and deactivate retired models.

    Returns a summary dict {"fetched": bool, "decommissioned": int, "ids": [...]}.
    Never raises — failures are logged and reported via "fetched": False.
    """
    if not SF_RELEASE_SYNC_ENABLED or not SF_RELEASE_NOTES_URL:
        log.info("SF release-notes sync disabled, skipping.")
        return {"fetched": False, "decommissioned": 0, "ids": [], "reason": "disabled"}

    try:
        text = await _fetch_text(SF_RELEASE_NOTES_URL)
        retired_ids = _parse_decommissioned(text)
    except Exception as e:
        log.warning("SF release-notes sync failed (data left intact): %s", e)
        return {"fetched": False, "decommissioned": 0, "ids": [], "reason": str(e)}

    if not retired_ids:
        log.info("SF release-notes: no retired models detected.")
        return {"fetched": True, "decommissioned": 0, "ids": []}

    log.info("SF release-notes: detected %d retired model ids.", len(retired_ids))

    # Only SiliconFlow channels carry these ids.
    deactivated = []
    with Session(engine) as session:
        sf_channels = {
            c.id for c in session.exec(
                select(Channel).where(Channel.provider_type == "siliconflow")
            ).all()
        }
        if not sf_channels:
            return {"fetched": True, "decommissioned": 0, "ids": sorted(retired_ids)}

        models = session.exec(
            select(Model).where(Model.channel_id.in_(sf_channels))
        ).all()
        for m in models:
            if m.model_id in retired_ids and m.is_active:
                m.is_active = False
                session.add(m)
                deactivated.append(m.model_id)
        if deactivated:
            session.commit()

    log.info("SF release-notes: deactivated %d models: %s", len(deactivated), deactivated)
    return {
        "fetched": True,
        "decommissioned": len(deactivated),
        "ids": sorted(retired_ids),
        "deactivated": deactivated,
    }
