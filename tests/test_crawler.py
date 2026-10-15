"""Crawler, fetcher and parser behaviour against MockTransport fake sites."""

from __future__ import annotations

from datetime import date

import httpx2
import pytest
from sitekit import FakeClock, FakeSite, html, shop_site

from pss_seo_inspector.crawler import crawl_site
from pss_seo_inspector.fetcher import Fetcher
from pss_seo_inspector.imagesize import image_size
from pss_seo_inspector.parse import parse_html
from pss_seo_inspector.robots import parse_robots

TODAY = date(2026, 10, 12)


@pytest.mark.anyio
async def test_robots_disallow_is_never_requested() -> None:
    site = shop_site()
    crawl = await crawl_site(
        "https://shop.test", transport=site.transport(), clock=FakeClock(), today=TODAY
    )
    requested = [url for _, url in site.requests]
    assert not any("/private" in url for url in requested)
    assert not any(url.startswith("https://shop.test/cart") for url in requested)
    assert "https://shop.test/private/secret" in crawl.skipped_robots
    assert "https://shop.test/cart" in crawl.skipped_robots


@pytest.mark.anyio
async def test_shop_crawl_detects_platform_and_records_everything() -> None:
    site = shop_site()
    crawl = await crawl_site(
        "shop.test", transport=site.transport(), clock=FakeClock(), today=TODAY
    )
    assert crawl.crawl_id == "shop.test/2026-10-12"
    assert crawl.platform == "shopify"
    by_url = {p.url: p for p in crawl.pages}
    # non-HTML: recorded, not parsed
    pdf = by_url["https://shop.test/files/manual.pdf"]
    assert pdf.status == 200 and pdf.html is None
    # timeout: recorded with error, crawl continued
    slow = by_url["https://shop.test/slow"]
    assert slow.error == "timeout" and slow.status is None
    # redirect chain recorded hop by hop
    old = by_url["https://shop.test/old-page"]
    assert [h.status for h in old.chain] == [301, 200]
    assert old.final_url == "https://shop.test/pages/about"
    # sitemap index followed; sitemap-only URL fetched
    assert [s.kind for s in crawl.sitemaps] == ["index", "urlset"]
    assert by_url["https://shop.test/pages/orphan"].source == "sitemap"
    # every request identifies itself and only GET/HEAD were used (asserted in FakeSite)
    assert {m for m, _ in site.requests} <= {"GET", "HEAD"}
    # probes stored for offline audits
    assert {p.rule_id for p in crawl.probes} >= {"IDX-02", "IDX-09", "SHOP-01"}


@pytest.mark.anyio
async def test_rate_limit_with_fake_clock() -> None:
    site = FakeSite()
    base = "https://gen.test"
    links = "".join(f'<a href="/p{i}">Page {i}</a>' for i in range(1, 10))
    site.add(f"{base}/", html(url=f"{base}/", body=links))
    for i in range(1, 10):
        site.add(f"{base}/p{i}", html(url=f"{base}/p{i}"))
    clock = FakeClock()
    crawl = await crawl_site(
        base,
        max_pages=10,
        rate=2.0,
        transport=site.transport(),
        clock=clock,
        today=TODAY,
    )
    assert len(crawl.pages) == 10
    assert clock.now >= 4.5
    assert crawl.platform == "generic"


@pytest.mark.anyio
async def test_429_retries_after_header_and_completes_the_page() -> None:
    site = FakeSite()
    base = "https://limited.test"
    site.add(f"{base}/robots.txt", "User-agent: *\n", content_type="text/plain")
    site.add(f"{base}/", html(url=f"{base}/"))
    site.add(f"{base}/p", html(url=f"{base}/p"))
    sequence = iter(
        [
            httpx2.Response(429, headers={"Retry-After": "3"}),
            httpx2.Response(
                200,
                headers={"content-type": "text/html"},
                content=html(url=f"{base}/"),
            ),
        ]
    )
    site.handlers[f"{base}/"] = lambda request: next(sequence)
    clock = FakeClock()
    crawl = await crawl_site(
        base,
        max_pages=2,
        transport=site.transport(),
        clock=clock,
        today=TODAY,
        quick=True,
    )
    assert not crawl.rate_limited
    assert crawl.pages[0].status == 200
    assert site.requests.count(("GET", f"{base}/")) == 2
    assert clock.now >= 5  # Retry-After plus the new five-second minimum gap.


@pytest.mark.anyio
async def test_repeated_429_stops_the_crawl_and_marks_it_incomplete() -> None:
    site = FakeSite()
    base = "https://limited.test"
    site.add(f"{base}/robots.txt", "User-agent: *\n", content_type="text/plain")
    site.add(f"{base}/", "too many requests", status=429, headers={"Retry-After": "2"})
    clock = FakeClock()
    crawl = await crawl_site(
        base,
        max_pages=5,
        transport=site.transport(),
        clock=clock,
        today=TODAY,
    )
    assert crawl.rate_limited
    assert crawl.pages[0].status == 429
    assert "repeated HTTP 429" in (crawl.pages[0].error or "")
    assert site.requests.count(("GET", f"{base}/")) == 2
    assert not any("sitemap" in url for _, url in site.requests)
    assert clock.now >= 2


@pytest.mark.anyio
async def test_max_pages_caps_the_crawl() -> None:
    site = shop_site()
    crawl = await crawl_site(
        "https://shop.test",
        max_pages=2,
        transport=site.transport(),
        clock=FakeClock(),
        today=TODAY,
    )
    assert len(crawl.pages) == 2 and crawl.capped


@pytest.mark.anyio
async def test_redirect_loop_and_hop_limit() -> None:
    site = FakeSite()
    site.redirect("https://l.test/a", "https://l.test/b")
    site.redirect("https://l.test/b", "https://l.test/a")
    for i in range(12):
        site.redirect(f"https://l.test/h{i}", f"https://l.test/h{i + 1}")
    fetcher = Fetcher(transport=site.transport(), clock=FakeClock())
    loop = (await fetcher.get("https://l.test/a")).resource
    many = (await fetcher.get("https://l.test/h0")).resource
    await fetcher.aclose()
    assert loop.error == "redirect loop"
    assert many.error is not None and "10 redirects" in many.error
    assert len(many.chain) == 10


@pytest.mark.anyio
async def test_body_cap() -> None:
    site = FakeSite()
    site.add("https://big.test/", b"x" * 100)
    fetcher = Fetcher(transport=site.transport(), clock=FakeClock())
    result = await fetcher.get("https://big.test/", max_body=10)
    await fetcher.aclose()
    assert result.resource.truncated and len(result.body) == 10


def test_robots_wildcards_and_groups() -> None:
    robots = parse_robots(
        "User-agent: *\nDisallow: /collections/*sort_by*\nDisallow: /x$\nAllow: /x/y\n"
        "Disallow: /x/\n\nUser-agent: GPTBot\nDisallow: /\n"
    )
    assert not robots.allowed("pss-seo-inspector", "https://a/collections/c?sort_by=1")
    assert robots.allowed("pss-seo-inspector", "https://a/collections/c")
    assert not robots.allowed("pss-seo-inspector", "https://a/x")
    assert robots.allowed("pss-seo-inspector", "https://a/x/y")
    assert not robots.allowed("pss-seo-inspector", "https://a/x/z")
    assert not robots.allowed("gptbot", "https://a/")
    assert robots.allowed("claudebot", "https://a/")  # falls back to '*'


def test_parse_extracts_head_and_links() -> None:
    doc = parse_html(
        '<!doctype html><html lang="de"><head><title>T</title>'
        '<link rel="canonical" href="/c"><div></div>'
        '<meta name="robots" content="noindex"></head><body>'
        '<svg><title>icon</title></svg><h1><img src="/logo.png" alt="Logo"></h1>'
        '<h1>Real</h1><a href="/x?y=1#frag"><img src="/i.png" alt="Pic"></a>'
        '<link rel="alternate" hreflang="en" href="https://s.test/en">'
        '<script type="application/ld+json">{"@type":"Product"}</script>'
        "</body></html>",
        "https://s.test/page",
    )
    assert doc.lang == "de"
    assert doc.titles == ["T"]
    assert doc.canonicals == ["https://s.test/c"]
    assert doc.head.robots_outside_head == 1  # after a <div> the head is closed
    assert doc.h1 == ["Real"] and doc.h1_logo_only == 1
    assert doc.links[0].href == "https://s.test/x?y=1" and doc.links[0].text == "Pic"
    assert doc.hreflang[0].lang == "en"
    assert doc.jsonld == ['{"@type":"Product"}']


def test_image_size_headers() -> None:
    import struct

    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 1200, 630)
    gif = b"GIF89a" + struct.pack("<HH", 300, 200)
    jpeg = (
        b"\xff\xd8\xff\xe0"
        + struct.pack(">H", 4)
        + b"\0\0"
        + b"\xff\xc0"
        + struct.pack(">HBHH", 17, 8, 480, 640)
    )
    webp = (
        b"RIFF\0\0\0\0WEBPVP8X"
        + b"\0" * 8
        + (799).to_bytes(3, "little")
        + (599).to_bytes(3, "little")
    )
    assert image_size(png) == (1200, 630)
    assert image_size(gif) == (300, 200)
    assert image_size(jpeg) == (640, 480)
    assert image_size(webp) == (800, 600)
    assert image_size(b"nope") is None


@pytest.mark.anyio
async def test_rate_limiter_spacing_is_exact() -> None:
    from pss_seo_inspector.fetcher import RateLimiter

    clock = FakeClock()
    limiter = RateLimiter(2.0, clock)
    for _ in range(10):
        await limiter.wait()
    assert clock.now == pytest.approx(4.5)
