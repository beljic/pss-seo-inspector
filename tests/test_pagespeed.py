"""Unit tests for the PageSpeed Insights API client."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from pss_seo_inspector.pagespeed import fetch_pagespeed, run_pagespeed

_PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def _full_psi_response(
    *,
    score: float = 0.95,
    lcp: float = 1200.0,
    cls: float = 0.05,
    inp: float = 80.0,
    fcp: float = 900.0,
    tbt: float = 50.0,
    ttfb: float = 200.0,
) -> dict[str, Any]:
    return {
        "lighthouseResult": {
            "categories": {
                "performance": {"score": score},
            },
            "audits": {
                "largest-contentful-paint": {"numericValue": lcp},
                "cumulative-layout-shift": {"numericValue": cls},
                "experimental-interaction-to-next-paint": {"numericValue": inp},
                "first-contentful-paint": {"numericValue": fcp},
                "total-blocking-time": {"numericValue": tbt},
                "server-response-time": {"numericValue": ttfb},
            },
        }
    }


def _make_transport(
    response_body: bytes | str | None = None,
    *,
    status: int = 200,
    raise_exc: Exception | None = None,
) -> httpx2.MockTransport:
    """Build a MockTransport that returns a fixed response or raises an exception."""

    def _handle(request: httpx2.Request) -> httpx2.Response:
        if raise_exc is not None:
            raise raise_exc
        body = response_body or b""
        if isinstance(body, str):
            body = body.encode()
        return httpx2.Response(status, content=body)

    return httpx2.MockTransport(_handle)


@pytest.mark.anyio
async def test_fetch_pagespeed_success_full_response() -> None:
    data = _full_psi_response()
    transport = _make_transport(json.dumps(data).encode())

    result = await fetch_pagespeed(
        "https://example.com", api_key=None, transport=transport
    )

    assert result.url == "https://example.com"
    assert result.strategy == "mobile"
    assert result.error is None
    assert result.score == pytest.approx(95.0)
    assert result.lcp_ms == pytest.approx(1200.0)
    assert result.cls == pytest.approx(0.05)
    assert result.inp_ms == pytest.approx(80.0)
    assert result.fcp_ms == pytest.approx(900.0)
    assert result.tbt_ms == pytest.approx(50.0)
    assert result.ttfb_ms == pytest.approx(200.0)


@pytest.mark.anyio
async def test_fetch_pagespeed_http_error_returns_error_result() -> None:
    transport = _make_transport(b"Internal Server Error", status=500)

    result = await fetch_pagespeed(
        "https://example.com", api_key=None, transport=transport
    )

    assert result.error is not None
    assert result.score is None
    assert result.lcp_ms is None


@pytest.mark.anyio
async def test_fetch_pagespeed_timeout_returns_error_result() -> None:
    mock_request = httpx2.Request("GET", _PSI_URL)
    transport = _make_transport(
        raise_exc=httpx2.ReadTimeout("timed out", request=mock_request)
    )

    result = await fetch_pagespeed(
        "https://example.com", api_key=None, transport=transport
    )

    assert result.error is not None
    assert "timed out" in result.error


@pytest.mark.anyio
async def test_fetch_pagespeed_missing_metric_field_is_none() -> None:
    data = _full_psi_response()
    # Remove CLS metric
    del data["lighthouseResult"]["audits"]["cumulative-layout-shift"]
    transport = _make_transport(json.dumps(data).encode())

    result = await fetch_pagespeed(
        "https://example.com", api_key=None, transport=transport
    )

    assert result.cls is None
    # Other fields still populated
    assert result.score == pytest.approx(95.0)
    assert result.lcp_ms == pytest.approx(1200.0)
    assert result.error is None


@pytest.mark.anyio
async def test_run_pagespeed_caps_at_five_urls() -> None:
    call_count = 0

    def _handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal call_count
        call_count += 1
        return httpx2.Response(200, content=json.dumps(_full_psi_response()).encode())

    transport = httpx2.MockTransport(_handle)
    urls = [f"https://example.com/page{i}" for i in range(6)]

    results = await run_pagespeed(urls, api_key=None, transport=transport)

    assert len(results) == 5
    assert call_count == 5


@pytest.mark.anyio
async def test_fetch_pagespeed_with_api_key_includes_key_param() -> None:
    received_params: dict[str, str] = {}

    def _handle(request: httpx2.Request) -> httpx2.Response:
        received_params.update(dict(request.url.params))
        return httpx2.Response(200, content=json.dumps(_full_psi_response()).encode())

    transport = httpx2.MockTransport(_handle)

    await fetch_pagespeed(
        "https://example.com", api_key="my-secret-key", transport=transport
    )

    assert received_params.get("key") == "my-secret-key"
    assert received_params.get("strategy") == "mobile"
    assert received_params.get("category") == "performance"


@pytest.mark.anyio
async def test_fetch_pagespeed_no_api_key_omits_key_param() -> None:
    received_params: dict[str, str] = {}

    def _handle(request: httpx2.Request) -> httpx2.Response:
        received_params.update(dict(request.url.params))
        return httpx2.Response(200, content=json.dumps(_full_psi_response()).encode())

    transport = httpx2.MockTransport(_handle)

    await fetch_pagespeed("https://example.com", api_key=None, transport=transport)

    assert "key" not in received_params


@pytest.mark.anyio
async def test_fetch_pagespeed_malformed_200_body_is_error_result() -> None:
    # A 200 whose body is not JSON must degrade to an error result, not crash.
    transport = _make_transport(b"<html>not json</html>")

    result = await fetch_pagespeed(
        "https://example.com", api_key=None, transport=transport
    )

    assert result.error is not None
    assert result.score is None
    assert result.lcp_ms is None


@pytest.mark.anyio
async def test_fetch_pagespeed_200_missing_lighthouse_result() -> None:
    # A valid-JSON 200 with no lighthouseResult yields all-None metrics, no crash.
    transport = _make_transport(json.dumps({"captchaResult": "ok"}).encode())

    result = await fetch_pagespeed(
        "https://example.com", api_key=None, transport=transport
    )

    assert result.error is None
    assert result.score is None
    assert result.lcp_ms is None


@pytest.mark.anyio
async def test_fetch_pagespeed_error_does_not_leak_api_key() -> None:
    # The HTTP error message embeds the request URL (which carries key=...);
    # the stored error must have the key redacted.
    mock_request = httpx2.Request(
        "GET", f"{_PSI_URL}?url=https://example.com&key=super-secret-key"
    )

    def _handle(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout(
            f"timed out for {mock_request.url}", request=mock_request
        )

    transport = httpx2.MockTransport(_handle)

    result = await fetch_pagespeed(
        "https://example.com", api_key="super-secret-key", transport=transport
    )

    assert result.error is not None
    assert "super-secret-key" not in result.error
    assert "REDACTED" in result.error
