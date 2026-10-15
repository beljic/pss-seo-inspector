"""Google PageSpeed Insights API client."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx2
from pydantic import BaseModel

log = logging.getLogger(__name__)

_PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_KEY_RE = re.compile(r"([?&]key=)[^&\s]+", re.IGNORECASE)


def _scrub_key(text: str) -> str:
    """Redact a `key=...` query value so the API key never reaches logs/reports."""
    return _KEY_RE.sub(r"\1REDACTED", text)


class PageSpeedResult(BaseModel):
    url: str
    strategy: str
    score: float | None = None
    lcp_ms: float | None = None
    cls: float | None = None
    inp_ms: float | None = None
    fcp_ms: float | None = None
    tbt_ms: float | None = None
    ttfb_ms: float | None = None
    error: str | None = None


def _safe_float(data: dict[str, Any], *keys: str) -> float | None:
    """Traverse nested dicts and return a float value, or None if missing."""
    node: Any = data
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if node is None:
        return None
    try:
        return float(node)
    except (TypeError, ValueError):
        return None


async def fetch_pagespeed(
    url: str,
    *,
    api_key: str | None,
    strategy: str = "mobile",
    timeout: float = 30.0,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> PageSpeedResult:
    """Fetch one PSI result for *url*.  Returns a result with error set on failure."""
    params: dict[str, str] = {
        "url": url,
        "strategy": strategy,
        "category": "performance",
    }
    if api_key is not None:
        params["key"] = api_key

    try:
        kwargs: dict[str, Any] = {"timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        async with httpx2.AsyncClient(**kwargs) as client:
            response = await client.get(_PSI_URL, params=params)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        lhr: dict[str, Any] = data.get("lighthouseResult", {})
        audits: dict[str, Any] = lhr.get("audits", {})

        raw_score = _safe_float(lhr, "categories", "performance", "score")
        score = raw_score * 100 if raw_score is not None else None

        return PageSpeedResult(
            url=url,
            strategy=strategy,
            score=score,
            lcp_ms=_safe_float(audits, "largest-contentful-paint", "numericValue"),
            cls=_safe_float(audits, "cumulative-layout-shift", "numericValue"),
            inp_ms=_safe_float(
                audits, "experimental-interaction-to-next-paint", "numericValue"
            ),
            fcp_ms=_safe_float(audits, "first-contentful-paint", "numericValue"),
            tbt_ms=_safe_float(audits, "total-blocking-time", "numericValue"),
            ttfb_ms=_safe_float(audits, "server-response-time", "numericValue"),
        )
    except (
        httpx2.HTTPError,
        httpx2.TimeoutException,
        KeyError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        error = _scrub_key(str(exc))
        log.warning("PSI fetch failed for %s: %s", url, error)
        return PageSpeedResult(url=url, strategy=strategy, error=error)
    except Exception as exc:  # noqa: BLE001 — PSI must never crash the audit
        error = _scrub_key(str(exc))
        log.warning("PSI fetch unexpected error for %s: %s", url, error)
        return PageSpeedResult(url=url, strategy=strategy, error=error)


async def run_pagespeed(
    urls: list[str],
    *,
    api_key: str | None,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> list[PageSpeedResult]:
    """Fetch PSI results for up to 5 URLs sequentially."""
    results: list[PageSpeedResult] = []
    for url in urls[:5]:
        result = await fetch_pagespeed(url, api_key=api_key, transport=transport)
        results.append(result)
    return results
