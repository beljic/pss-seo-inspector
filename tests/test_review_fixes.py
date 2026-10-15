"""Regression tests for the review findings."""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import pytest
from sitekit import (
    ORIGIN,
    FakeClock,
    FakeSite,
    good_crawl,
    good_pages,
    good_probes,
    html,
    page,
    probe,
    shop_site,
    sitemap,
)

from pss_seo_inspector import server
from pss_seo_inspector.crawler import Crawler, crawl_site
from pss_seo_inspector.engine import run_audit
from pss_seo_inspector.fetcher import decode
from pss_seo_inspector.models import Hop
from pss_seo_inspector.parse import parse_html
from pss_seo_inspector.platform import detect
from pss_seo_inspector.robots import from_status, parse_robots
from pss_seo_inspector.rules.base import ProbeSpec, Rule
from pss_seo_inspector.urlutil import absolutize, normalize

TODAY = date(2026, 10, 12)
A = f"{ORIGIN}/a"
HOME = f"{ORIGIN}/"


async def call(name: str, args: dict[str, Any]) -> Any:
    from mcp import Client

    async with Client(server.mcp) as client:
        return await client.call_tool(name, args)


def use_site(monkeypatch: pytest.MonkeyPatch, site: FakeSite) -> None:
    monkeypatch.setattr(server, "transport", site.transport())
    monkeypatch.setattr(server, "clock", FakeClock())
    monkeypatch.setattr(server, "today", TODAY)


# -- platform ---------------------------------------------------------------


def test_ordinary_html_is_generic() -> None:
    doc = (
        '<link rel="icon" type="image/png" href="/wp-content/uploads/f.png">'
        '<img src="/wp-content/themes/t/image/a.png">'
    )
    assert detect(doc, {})[0] == "generic"


def test_magento_and_woocommerce_markers() -> None:
    assert detect("<p>x</p>", {"X-Magento-Cache-Debug": "HIT"})[0] == "magento2"
    assert detect('<script src="/static/frontend/Vendor/t/x.js">', {})[0] == "magento2"
    woo = (
        '<body class="WooCommerce-page"><link href="/wp-content/plugins/woocommerce/">'
    )
    assert detect(woo, {})[0] == "woocommerce"


# -- robots -----------------------------------------------------------------


def test_robots_status_handling_and_bom() -> None:
    assert not from_status(429, "").allowed("x", "https://a/")
    assert not from_status(503, "").allowed("x", "https://a/")
    assert from_status(404, "").allowed("x", "https://a/")
    assert from_status(None, "", "more than 10 redirects").allowed("x", "https://a/")
    assert not from_status(None, "", "timeout").allowed("x", "https://a/")
    bom = parse_robots("﻿User-agent: *\nDisallow: /p\n")
    assert not bom.allowed("x", "https://a/p")


@pytest.mark.anyio
async def test_robots_503_means_only_robots_is_requested() -> None:
    site = FakeSite()
    for origin in (
        "https://down.test",
        "http://down.test",
        "https://www.down.test",
        "http://www.down.test",
    ):
        site.add(f"{origin}/robots.txt", "err", status=503)
    site.add("https://down.test/", html(url="https://down.test/"))
    await crawl_site(
        "https://down.test/", transport=site.transport(), clock=FakeClock(), today=TODAY
    )
    urls = {u for _, u in site.requests}
    assert "https://down.test/robots.txt" in urls
    assert all(u.endswith("/robots.txt") for u in urls), urls


@pytest.mark.anyio
async def test_crawl_delay_is_honoured() -> None:
    site = FakeSite()
    site.add(
        "https://slow.test/robots.txt",
        "User-agent: *\nCrawl-delay: 3\n",
        content_type="text/plain",
    )
    site.add(
        "https://slow.test/", html(url="https://slow.test/", body='<a href="/b">B</a>')
    )
    site.add("https://slow.test/b", html(url="https://slow.test/b"))
    clock = FakeClock()
    crawler = Crawler(
        "https://slow.test/",
        transport=site.transport(),
        clock=clock,
        today=TODAY,
        rules=[],
        quick=True,
        max_pages=2,
    )
    await crawler.run()
    assert len(site.requests) == 3
    assert clock.now >= 0.5 + 3.0  # robots→page at 1/rate, then page→page at delay


# -- IDX-11 / IDX-12 ---------------------------------------------------------


def test_idx11_5xx_and_401() -> None:
    crawl = good_crawl()
    assert crawl.robots is not None
    crawl.robots.status = 503
    [finding] = run_audit(crawl, None, ["IDX-11"]).findings
    assert "503" in finding.evidence and finding.severity == "high"
    crawl.robots.status = 401
    [finding] = run_audit(crawl, None, ["IDX-11"]).findings
    assert "like a 404" in finding.evidence and finding.severity == "low"


def test_idx12_disallowed_linked_urls_go_to_manual() -> None:
    crawl = good_crawl()
    crawl.skipped_robots = [A]
    result = run_audit(crawl, None, ["IDX-12"])
    assert "IDX-12" not in result.passed
    assert any("cannot be verified" in m.description for m in result.manual)


# -- engine -----------------------------------------------------------------


def test_no_html_pages_sends_page_rules_to_manual() -> None:
    crawl = good_crawl(pages=[page(HOME, None, status=500)])
    result = run_audit(crawl, None, ["META-04", "IDX-03"])
    assert result.passed == []
    assert {m.description for m in result.manual} == {"No HTML pages crawled"}


def test_raising_rule_becomes_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    from pss_seo_inspector import engine
    from pss_seo_inspector.models import Severity
    from pss_seo_inspector.rules.base import Issue

    def boom(ctx: object) -> list[Issue]:
        raise RuntimeError("kaputt")

    def empty_url(ctx: object) -> list[Issue]:
        return [Issue("", "evidence")]

    rules = [
        Rule(
            id="T-1",
            category="t",
            severity=Severity.low,
            description="t",
            check=boom,
            fix={"generic": "x"},
        ),
        Rule(
            id="T-2",
            category="t",
            severity=Severity.low,
            description="t",
            check=empty_url,
            fix={"generic": "x"},
        ),
    ]
    monkeypatch.setattr(engine, "select_rules", lambda ids: rules)
    result = run_audit(good_crawl(), None)
    assert [m.description for m in result.manual] == [
        "Rule raised RuntimeError: kaputt"
    ]
    assert result.findings[0].url == f"{ORIGIN}/"


# -- parse / url / charset ----------------------------------------------------


def test_base_href_is_used() -> None:
    doc = parse_html(
        '<html><head><base href="https://cdn.test/sub/"><link rel="canonical" '
        'href="c"></head><body><a href="x">X</a></body></html>',
        "https://s.test/page",
    )
    assert doc.canonicals == ["https://cdn.test/sub/c"]
    assert doc.links[0].href == "https://cdn.test/sub/x"


def test_invalid_port_does_not_raise() -> None:
    assert absolutize("https://a.test/", "http://x.com:abc/") is None
    assert normalize("http://x.com:abc/") == "http://x.com:abc/"


def test_charset_sniffed_from_meta() -> None:
    body = '<meta charset="windows-1252"><p>caf\xe9</p>'.encode("latin-1")
    assert "café" in decode(body, "text/html")
    assert "café" not in decode(body, "text/html; charset=utf-8")


# -- crawler ------------------------------------------------------------------


@pytest.mark.anyio
async def test_redirect_target_is_not_fetched_twice() -> None:
    site = FakeSite()
    base = "https://r.test"
    site.add(
        f"{base}/",
        html(
            url=f"{base}/",
            title="Home of the redirect test site",
            body='<a href="/old">Old</a><a href="/about">About</a>',
        ),
    )
    site.redirect(f"{base}/old", f"{base}/about")
    site.add(
        f"{base}/about",
        html(url=f"{base}/about", title="About the redirect test site page"),
    )
    crawl = await crawl_site(
        base, transport=site.transport(), clock=FakeClock(), today=TODAY
    )
    assert [u for m, u in site.requests if m == "GET"].count(f"{base}/about") == 1
    old = next(p for p in crawl.pages if p.url == f"{base}/old")
    assert old.html is not None and old.final_url == f"{base}/about"
    assert sum(1 for p in crawl.pages if p.html is not None) == 2
    evidence = [f.evidence for f in run_audit(crawl, None, ["META-01"]).findings]
    assert not any("Same title" in e for e in evidence)


@pytest.mark.anyio
async def test_head_405_falls_back_to_get() -> None:
    site = FakeSite()
    base = "https://h.test"
    site.add(f"{base}/", html(url=f"{base}/", body='<a href="/x">X</a>'))

    def no_head(request: httpx2.Request) -> httpx2.Response:
        if request.method == "HEAD":
            return httpx2.Response(405)
        return httpx2.Response(200, headers={"content-type": "text/html"}, content=b"")

    site.handlers[f"{base}/x"] = no_head
    crawler = Crawler(
        base,
        max_pages=1,
        transport=site.transport(),
        clock=FakeClock(),
        today=TODAY,
        rules=[],
    )
    crawl = await crawler.run()
    assert ("HEAD", f"{base}/x") in site.requests and (
        "GET",
        f"{base}/x",
    ) in site.requests
    [check] = [c for c in crawl.link_checks if c.url == f"{base}/x"]
    assert check.status == 200


@pytest.mark.anyio
async def test_gzip_sitemap_and_foreign_index_children() -> None:
    site = FakeSite()
    base = "https://g.test"
    site.add(
        f"{base}/robots.txt",
        f"User-agent: *\nSitemap: {base}/index.xml\n",
        content_type="text/plain",
    )
    site.add(
        f"{base}/index.xml",
        "<sitemapindex><sitemap><loc>https://g.test/s.xml.gz</loc></sitemap>"
        "<sitemap><loc>https://evil.test/s.xml</loc></sitemap>"
        "<sitemap><loc>/relative.xml</loc></sitemap></sitemapindex>",
        content_type="application/xml",
    )
    xml = (
        f"<urlset><url><loc>{base}/p</loc><lastmod>2026-10-01</lastmod></url></urlset>"
    )
    site.add(
        f"{base}/s.xml.gz",
        gzip.compress(xml.encode()),
        content_type="application/x-gzip",
    )
    site.add(f"{base}/", html(url=f"{base}/"))
    crawl = await crawl_site(
        base, transport=site.transport(), clock=FakeClock(), today=TODAY
    )
    assert [s.url for s in crawl.sitemaps] == [f"{base}/index.xml", f"{base}/s.xml.gz"]
    assert crawl.sitemaps[1].entries[0].loc == f"{base}/p"
    assert not any("evil.test" in u or "relative" in u for _, u in site.requests)


@pytest.mark.anyio
async def test_raising_probes_does_not_kill_crawl() -> None:
    from pss_seo_inspector.models import Severity

    def bad(ctx: object) -> list[ProbeSpec]:
        raise ValueError("bad probe")

    rule = Rule(
        id="T-9",
        category="t",
        severity=Severity.low,
        description="t",
        check=lambda ctx: [],
        probes=bad,
        fix={"generic": "x"},
    )
    site = FakeSite()
    site.add("https://p.test/", html(url="https://p.test/"))
    crawl = await Crawler(
        "https://p.test/",
        transport=site.transport(),
        clock=FakeClock(),
        today=TODAY,
        rules=[rule],
    ).run()
    assert crawl.probe_errors == ["T-9: ValueError: bad probe"]


@pytest.mark.anyio
async def test_audit_url_requests_only_page_and_page_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = shop_site()
    use_site(monkeypatch, site)
    result = await call("audit_url", {"url": "https://shop.test/pages/about"})
    assert result.is_error is False
    assert {u for _, u in site.requests} == {
        "https://shop.test/robots.txt",
        "https://shop.test/pages/about",
    }
    bad = await call("audit_url", {"url": "https://shop.test/", "rules": ["IDX-05"]})
    assert bad.is_error is True and "IDX-05" in bad.content[0].text


# -- server / storage -----------------------------------------------------------


@pytest.mark.anyio
async def test_invalid_host_rejected_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FakeSite()
    use_site(monkeypatch, site)
    result = await call("crawl_site", {"url": "https://_bad_host_/"})
    assert result.is_error is True and "Invalid URL host" in result.content[0].text
    assert site.requests == []


@pytest.mark.anyio
async def test_same_day_recrawl_removes_stale_findings(
    monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path
) -> None:
    use_site(monkeypatch, shop_site())
    await call("audit_site", {"url": "https://shop.test"})
    out = isolated_dirs / "reports" / "shop.test" / "2026-10-12"
    assert (out / "findings.json").is_file()
    await call("crawl_site", {"url": "https://shop.test"})
    assert not (out / "findings.json").exists() and not (out / "report.md").exists()
    stale = await call("get_findings", {"crawl_id": "shop.test/2026-10-12"})
    assert stale.is_error is True
    assert json.loads((out / "crawl.json").read_text())["crawl_id"]


# -- rule fixes -------------------------------------------------------------------


def test_idx06_ignores_images_and_percent_escapes() -> None:
    body = (
        f'<a href="{A}">About</a><a href="/caf%C3%A9">Cafe</a>'
        '<img src="/cdn/shop/files/Hero_Image.JPG" alt="x">'
    )
    pages = good_pages()
    pages[0] = page(
        HOME, html(url=HOME, title="Home page of the example test shop", body=body)
    )
    assert run_audit(good_crawl(pages=pages), None, ["IDX-06"]).findings == []


def test_idx09_two_hop_chain_is_flagged() -> None:
    chain = [
        Hop(url="http://www.ex.test/", status=301, location="https://www.ex.test/"),
        Hop(url="https://www.ex.test/", status=301, location=f"{ORIGIN}/"),
        Hop(url=f"{ORIGIN}/", status=200),
    ]
    probes = [
        *good_probes(),
        probe(
            "IDX-09",
            "http://www.ex.test/",
            "http://www.ex.test/",
            kind="status",
            chain=chain,
        ),
    ]
    [finding] = run_audit(good_crawl(probes=probes), None, ["IDX-09"]).findings
    assert "2 hops" in finding.evidence


def test_idx10_relative_and_empty_entries() -> None:
    from pss_seo_inspector.rules.base import ProbeContext
    from pss_seo_inspector.rules.indexing import _idx10_probes
    from pss_seo_inspector.site_config import SiteConfig

    ctx = ProbeContext(
        crawl=good_crawl(),
        token="t",
        site_config=SiteConfig(legacy_urls=["/old.html", " ", ""]),
    )
    assert [s.url for s in _idx10_probes(ctx)] == [f"{ORIGIN}/old.html"]


def test_idx08_skips_foreign_host_entries() -> None:
    crawl = good_crawl(sitemaps=[sitemap(HOME, A, "https://other.test/x")])
    result = run_audit(crawl, None, ["IDX-08"])
    assert result.findings == [] and "IDX-08" in result.passed


def test_meta05_unprobed_images_are_manual() -> None:
    probes = [p for p in good_probes() if p.rule_id != "META-05"]
    result = run_audit(good_crawl(probes=probes), None, ["META-05"])
    assert any("not probed" in m.description for m in result.manual)


def test_wp01_redirected_attachment_passes_and_mag01_raw_route() -> None:
    redirected = probe(
        "WP-01",
        "5",
        f"{ORIGIN}/?attachment_id=5",
        text="<html></html>",
        chain=[
            Hop(
                url=f"{ORIGIN}/?attachment_id=5", status=301, location=f"{ORIGIN}/post"
            ),
            Hop(url=f"{ORIGIN}/post", status=200),
        ],
    )
    crawl = good_crawl(platform="woocommerce", probes=[*good_probes(), redirected])
    assert run_audit(crawl, None, ["WP-01"]).findings == []

    raw = page(
        A,
        html(url=A, canonical=f"{ORIGIN}/catalog/product/view/id/7").replace(
            "<body>", '<body class="catalog-product-view">'
        ),
    )
    crawl = good_crawl(platform="magento2", pages=[good_pages()[0], raw])
    assert run_audit(crawl, None, ["MAG-01"]).findings == []
