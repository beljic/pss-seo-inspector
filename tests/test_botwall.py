"""Bot-protection challenges are detected and stop the crawl; never bypassed."""

from __future__ import annotations

from datetime import date

import httpx2
import pytest
from sitekit import FakeClock, FakeSite, fixture_text, html

from pss_seo_inspector.botwall import CLOUDFLARE, challenge_reason
from pss_seo_inspector.crawler import crawl_site
from pss_seo_inspector.engine import run_audit

TODAY = date(2026, 10, 12)
CHALLENGE = fixture_text("challenge", "cloudflare.html")
BASE = "https://shop.test"


def test_challenge_page_is_detected_by_body_markers() -> None:
    body = CHALLENGE.encode()
    assert challenge_reason(403, {}, body) == CLOUDFLARE
    assert challenge_reason(503, {}, body) == CLOUDFLARE


def test_cf_mitigated_header_is_detected_without_a_body() -> None:
    assert challenge_reason(403, {"cf-mitigated": "challenge"}, b"") == CLOUDFLARE


def test_plain_403_and_normal_pages_are_not_challenges() -> None:
    assert challenge_reason(403, {}, b"<html><body>Forbidden</body></html>") is None
    assert challenge_reason(200, {}, html(url=f"{BASE}/").encode()) is None
    # markers only count on a challenge status, not in an article about Cloudflare
    assert challenge_reason(200, {}, CHALLENGE.encode()) is None


def _walled_transport(requests: list[str]) -> httpx2.MockTransport:
    """Every path, robots.txt included, answers with the Cloudflare challenge."""

    def _handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method in ("GET", "HEAD")
        assert request.headers["user-agent"] == "pss-seo-inspector/1.0"
        requests.append(str(request.url))
        headers = {"content-type": "text/html", "cf-mitigated": "challenge"}
        return httpx2.Response(403, headers=headers, content=CHALLENGE.encode())

    return httpx2.MockTransport(_handle)


@pytest.mark.anyio
async def test_three_consecutive_challenges_stop_the_crawl() -> None:
    requests: list[str] = []
    crawl = await crawl_site(
        BASE,
        max_pages=50,
        transport=_walled_transport(requests),
        clock=FakeClock(),
        today=TODAY,
    )
    assert crawl.bot_blocked
    assert crawl.bot_protection == CLOUDFLARE
    assert crawl.blocked_count == 3
    assert len(requests) == 3, requests  # third challenge in a row stops it
    start = crawl.pages[0]
    assert start.blocked_by == CLOUDFLARE
    assert start.html is None  # the challenge HTML is never parsed as the site
    assert crawl.probes == []


@pytest.mark.anyio
async def test_a_single_blocked_link_is_counted_not_reported_as_broken() -> None:
    site = FakeSite()
    site.add(f"{BASE}/robots.txt", "User-agent: *\n", content_type="text/plain")
    site.add(f"{BASE}/", html(url=f"{BASE}/", body='<a href="/wall">Wall</a>'))
    site.add(
        f"{BASE}/wall", CHALLENGE, status=403, headers={"cf-mitigated": "challenge"}
    )
    crawl = await crawl_site(
        BASE, max_pages=5, transport=site.transport(), clock=FakeClock(), today=TODAY
    )
    assert not crawl.bot_blocked
    assert crawl.blocked_count == 1
    result = run_audit(crawl, None, ["IDX-05"])
    assert not [f for f in result.findings if f.url == f"{BASE}/wall"]
    assert any(f"{BASE}/wall" in m.description for m in result.manual)
