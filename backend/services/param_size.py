"""Infer a model's parameter count (in billions) from its model id.

Used by the ``auto:smart`` router to prefer larger (generally more capable)
models. There is no authoritative param-size field across providers, so the
id is parsed by convention:

* MoE ids like ``nemotron-3-ultra-550b-a55b`` carry two counts — total (550b)
  and activated (a55b). The activated count is the one that reflects real
  inference quality, so it wins.
* Dense ids like ``qwen2.5-72b`` carry a single count, used directly.
* Ids without any numeric size marker (``gpt-4o``, ``claude``, ``glm``) return
  None; the whitelist's ``param_size`` override is the fallback for those.
"""
from __future__ import annotations
import re

# MoE: "550b-a55b" / "35b-a3b" — capture activated params (second group).
_MOE_RE = re.compile(r"(\d+(?:\.\d+)?)b-a(\d+(?:\.\d+)?)b", re.IGNORECASE)

# Dense: a "<n>b" not preceded by another digit or dot (so "550" isn't partly
# rematched) and not part of a larger token like "bge" (require a non-letter
# before the digits). The trailing "-a" exclusion is handled by running MoE
# first and stripping it.
_DENSE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)b\b", re.IGNORECASE)


def _last_segment(model_id: str) -> str:
    """Reduce to the informative tail: drop ``:free`` and any ``org/`` prefix."""
    s = model_id.split(":free", 1)[0]
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    return s


def parse_param_size(model_id: str) -> float | None:
    """Return the parameter count in billions, or None if it can't be parsed.

    >>> parse_param_size("qwen2.5-72b")
    72.0
    >>> parse_param_size("nvidia/nemotron-3-ultra-550b-a55b")
    55.0
    >>> parse_param_size("gpt-4o") is None
    True
    """
    if not model_id:
        return None
    seg = _last_segment(model_id)

    moe = _MOE_RE.search(seg)
    if moe:
        return float(moe.group(2))

    # Strip any MoE fragment so the dense regex can't rematch its total-params
    # half (e.g. "550b" inside "550b-a55b").
    dense_seg = _MOE_RE.sub(" ", seg)
    dense = _DENSE_RE.search(dense_seg)
    if dense:
        return float(dense.group(1))

    return None
