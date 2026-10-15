"""Builders for in-memory crawls and fake sites (no network)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2

from pss_seo_inspector.models import (
    Crawl,
    Hop,
    Page,
    Probe,
    Resource,
    RobotsFile,
    SitemapEntry,
    SitemapFile,
)
from pss_seo_inspector.parse import parse_html

FIXTURES = Path(__file__).parent / "fixtures"
ORIGIN = "https://ex.test"
GOOD_ROBOTS = (
    "User-agent: *\nDisallow: /private\nSitemap: https://ex.test/sitemap.xml\n"
)


def html(
    *,
    url: str,
    title: str = "A perfectly reasonable page title here",
    desc: str = (
        "A meta description that is long enough to pass the seventy character minimum."
    ),
    canonical: str | None = "self",
    h1: str = "Main heading",
    lang: str | None = "en",
    head_extra: str = "",
    body: str = "",
    og_width: str = "1200",
) -> str:
    canonical_tag = ""
    if canonical == "self":
        canonical_tag = f'<link rel="canonical" href="{url}">'
    elif canonical:
        canonical_tag = f'<link rel="canonical" href="{canonical}">'
    lang_attr = f' lang="{lang}"' if lang else ""
    return f"""<!DOCTYPE html>
<html{lang_attr}><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
{canonical_tag}
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{ORIGIN}/share.png">
<meta property="og:image:width" content="{og_width}">
<meta name="twitter:card" content="summary_large_image">
{head_extra}
</head><body>
<h1>{h1}</h1>
{body}
</body></html>"""


def page(
    url: str,
    text: str | None = None,
    *,
    status: int = 200,
    depth: int | None = 0,
    headers: dict[str, str] | None = None,
    chain: list[Hop] | None = None,
    final_url: str | None = None,
) -> Page:
    final = final_url or url
    parsed = parse_html(text, final) if text is not None else None
    return Page(
        url=url,
        final_url=final,
        status=status,
        chain=chain or [Hop(url=url, status=status)],
        headers=headers or {},
        content_type="text/html; charset=utf-8",
        depth=depth,
        html=parsed,
    )


def resource(url: str, status: int, chain: list[Hop] | None = None) -> Resource:
    hops = chain or [Hop(url=url, status=status)]
    return Resource(url=url, final_url=hops[-1].url, status=status, chain=hops)


def probe(
    rule_id: str,
    key: str,
    url: str,
    *,
    status: int = 200,
    kind: str = "page",
    text: str | None = None,
    chain: list[Hop] | None = None,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> Probe:
    hops = chain or [Hop(url=url, status=status)]
    final = hops[-1].url
    return Probe(
        rule_id=rule_id,
        key=key,
        url=url,
        final_url=final,
        status=status,
        chain=hops,
        kind=kind,  # type: ignore[arg-type]
        headers=headers or {},
        content_type="text/html",
        html=parse_html(text, final) if text is not None and kind == "page" else None,
        text=text if kind == "text" else None,
        **extra,
    )


def sitemap(*locs: str, lastmod: str | None = "2026-10-01") -> SitemapFile:
    entries = [SitemapEntry(loc=loc, lastmod=lastmod) for loc in locs]
    return SitemapFile(
        url=f"{ORIGIN}/sitemap.xml",
        final_url=f"{ORIGIN}/sitemap.xml",
        status=200,
        chain=[Hop(url=f"{ORIGIN}/sitemap.xml", status=200)],
        kind="urlset",
        entries=entries,
        entry_count=len(entries),
        uncompressed_bytes=1000,
    )


def robots(text: str = GOOD_ROBOTS, status: int = 200) -> RobotsFile:
    url = f"{ORIGIN}/robots.txt"
    return RobotsFile(
        url=url,
        final_url=url,
        status=status,
        chain=[Hop(url=url, status=status)],
        text=text,
        size_bytes=len(text.encode()),
    )


def make_crawl(
    pages: list[Page],
    *,
    platform: str = "generic",
    robots_text: str = GOOD_ROBOTS,
    sitemaps: list[SitemapFile] | None = None,
    probes: list[Probe] | None = None,
    link_checks: list[Resource] | None = None,
    **crawl_overrides: Any,
) -> Crawl:
    return Crawl(
        crawl_id="ex.test/2026-10-12",
        domain="ex.test",
        start_url=f"{ORIGIN}/",
        origin=ORIGIN,
        host="ex.test",
        fetched_at="2026-10-12T10:00:00+00:00",
        user_agent="pss-seo-inspector/1.0",
        rate=2.0,
        max_pages=200,
        platform=platform,
        pages=pages,
        robots=robots(robots_text),
        sitemaps=sitemaps
        if sitemaps is not None
        else [sitemap(f"{ORIGIN}/", f"{ORIGIN}/a")],
        probes=probes or [],
        link_checks=link_checks or [],
        **crawl_overrides,
    )


HOME_COPY = (
    "Our workshop designs optical filters that clip into light frames for drivers, "
    "gamers and anyone who spends long evenings in front of bright screens. Each "
    "filter starts as a sheet of tinted polymer that we cut, coat and inspect by "
    "hand before it leaves the bench. We test every batch against glare from "
    "oncoming headlights, office lamps and monitors, then record the results in a "
    "public log so customers can compare models before they order. Frames come in "
    "three widths and two finishes, and every filter swaps between them in "
    "seconds without tools. Shipping is free across the country, and orders placed "
    "before noon leave the warehouse the same afternoon. If a filter does not suit "
    "your eyes, send it back within fifteen days from delivery for a full refund. "
    "Our support team answers questions by email every weekday, usually within a "
    "few hours, and keeps spare parts for older frames in stock for at least five "
    "years after a model is retired from the catalogue."
)
ABOUT_COPY = (
    "The company began in a small garage where two engineers tried to fix their own "
    "eye strain after long night shifts at a hospital. They built the first "
    "prototype from salvaged camera lenses and a cheap reading frame, and friends "
    "soon asked for copies. Today a team of twelve people works from a converted "
    "print shop near the river, sharing one long table, a coffee machine and a "
    "stubborn dog called Pixel. We buy raw material from two suppliers we have "
    "visited in person, pay them on time and publish the prices we agreed. Every "
    "new design goes through a month of daily wear by volunteers who describe "
    "comfort, weight and clarity in plain notes. We keep the range small on "
    "purpose: fewer models mean better stock, faster repairs and honest advice. "
    "When a customer writes with an idea, it goes on the whiteboard in the "
    "kitchen, and several current features started exactly there. We plan to open "
    "a fitting room next spring so visitors can try every frame in person."
)
HOME_JSONLD = (
    '<script type="application/ld+json">{"@context":"https://schema.org",'
    '"@graph":[{"@type":"Organization","@id":"https://ex.test/#org",'
    '"name":"Example Shop","url":"https://ex.test/",'
    '"logo":"https://ex.test/logo.png","sameAs":["https://social.test/ex"]},'
    '{"@type":"WebSite","name":"Example Shop","alternateName":"ExShop",'
    '"url":"https://ex.test/"}]}</script>'
)


def good_pages() -> list[Page]:
    home = f"{ORIGIN}/"
    a = f"{ORIGIN}/a"
    return [
        page(
            home,
            html(
                url=home,
                title="Home page of the example test shop",
                head_extra=HOME_JSONLD,
                body=f'<a href="{a}">About our frames</a><p>{HOME_COPY}</p>',
            ),
            depth=0,
        ),
        page(
            a,
            html(
                url=a,
                title="About page of the example test shop",
                desc=(
                    "Another meta description that is long enough to pass the "
                    "seventy character minimum."
                ),
                body=f'<a href="{home}">Back to the home page</a><p>{ABOUT_COPY}</p>',
            ),
            depth=1,
        ),
    ]


def good_probes() -> list[Probe]:
    random_url = f"{ORIGIN}/pss-404-check-abc"
    return [
        probe(
            "IDX-02",
            "random",
            random_url,
            status=404,
            text='<html><head><meta name="robots" content="noindex"></head></html>',
        ),
        probe("IDX-17", "llms.txt", f"{ORIGIN}/llms.txt", kind="text", text="# llms"),
        probe(
            "META-05",
            f"{ORIGIN}/share.png",
            f"{ORIGIN}/share.png",
            kind="image",
            image_width=1200,
            image_height=630,
        ),
        probe(
            "IDX-09",
            "http://ex.test/",
            "http://ex.test/",
            kind="status",
            chain=[
                Hop(url="http://ex.test/", status=301, location=f"{ORIGIN}/"),
                Hop(url=f"{ORIGIN}/", status=200),
            ],
        ),
    ]


def good_crawl(**overrides: Any) -> Crawl:
    params: dict[str, Any] = {"probes": good_probes()}
    params.update(overrides)
    pages = params.pop("pages", None) or good_pages()
    return make_crawl(pages, **params)


# -- fake HTTP site --------------------------------------------------------


Route = tuple[int, dict[str, str], bytes]


@dataclass
class FakeSite:
    """A MockTransport-backed site; records every request."""

    routes: dict[str, Route] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)
    handlers: dict[str, Callable[[httpx2.Request], httpx2.Response]] = field(
        default_factory=dict
    )
    block_network: bool = False

    def add(
        self,
        url: str,
        body: str | bytes = "",
        status: int = 200,
        headers: dict[str, str] | None = None,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        data = body.encode() if isinstance(body, str) else body
        merged = {"content-type": content_type, **(headers or {})}
        self.routes[url] = (status, merged, data)

    def redirect(self, url: str, to: str, status: int = 301) -> None:
        self.routes[url] = (status, {"location": to}, b"")

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        if self.block_network:
            raise AssertionError(f"Network disabled, got {request.method} {url}")
        assert request.method in ("GET", "HEAD"), request.method
        assert request.headers["user-agent"] == "pss-seo-inspector/1.0"
        handler = self.handlers.get(url)
        if handler is not None:
            return handler(request)
        status, headers, body = self.routes.get(
            url,
            (
                404,
                {"content-type": "text/html"},
                b"<html><body>Not found</body></html>",
            ),
        )
        if request.method == "HEAD":
            body = b""
        return httpx2.Response(status, headers=headers, content=body)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def fixture_text(*parts: str) -> str:
    return (FIXTURES.joinpath(*parts)).read_text(encoding="utf-8")


def shop_site() -> FakeSite:
    """Shopify-like fake store at https://shop.test built from tests/fixtures/shop."""
    site = FakeSite()
    base = "https://shop.test"
    site.add(
        f"{base}/robots.txt",
        fixture_text("shop", "robots.txt"),
        content_type="text/plain",
    )
    site.add(
        f"{base}/sitemap.xml",
        fixture_text("shop", "sitemap.xml"),
        content_type="application/xml",
    )
    site.add(
        f"{base}/sitemap_pages_1.xml",
        fixture_text("shop", "sitemap_pages_1.xml"),
        content_type="application/xml",
    )
    for path, name in (
        ("/", "home.html"),
        ("/products/aim-filter", "product.html"),
        ("/collections/frames", "collection.html"),
        ("/pages/about", "about.html"),
    ):
        site.add(f"{base}{path}", fixture_text("shop", name), headers={"x-shopid": "1"})
    site.add(
        f"{base}/collections/frames/products/aim-filter",
        fixture_text("shop", "product.html"),
    )
    site.add(f"{base}/files/manual.pdf", b"%PDF-1.4", content_type="application/pdf")
    site.redirect(f"{base}/old-page", f"{base}/pages/about")
    site.redirect("http://shop.test/", f"{base}/")
    site.redirect("https://www.shop.test/", f"{base}/")
    site.redirect("http://www.shop.test/", f"{base}/")
    site.add(f"{base}/llms.txt", "# shop", content_type="text/plain")
    site.add(f"{base}/share.png", _png(1200, 630), content_type="image/png")

    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    site.handlers[f"{base}/slow"] = timeout
    return site


def _png(width: int, height: int) -> bytes:
    import struct

    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I4sII", 13, b"IHDR", width, height)
        + b"\0" * 20
    )
