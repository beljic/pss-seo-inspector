"""Breadth-first, same-host crawl plus the sitemap tree, link checks and probes."""

from __future__ import annotations

import gzip
import uuid
import xml.etree.ElementTree as ET
import zlib
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, date, datetime
from typing import Literal

import httpx2

from .fetcher import (
    IMAGE_RANGE_BYTES,
    USER_AGENT,
    Clock,
    Fetcher,
    decode,
    is_html,
)
from .imagesize import image_size
from .models import Crawl, Page, Probe, Resource, SitemapEntry, SitemapFile
from .parse import parse_html
from .platform import detect
from .rules.base import ProbeContext, ProbeSpec, Rule, all_rules
from .site_config import SiteConfig
from .urlutil import domain_of, host_of, normalize, origin_of

DEFAULT_MAX_PAGES = 200
MAX_LINK_CHECKS = 500
MAX_SITEMAP_FILES = 50
SITEMAP_MAX_BYTES = 50 * 1024 * 1024 + 1
PROBE_TEXT_MAX = 100 * 1024

PageSource = Literal["start", "link", "sitemap"]
Progress = Callable[[float, float | None, str | None], Awaitable[None]]


def ensure_url(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    return normalize(url)


class Crawler:
    def __init__(
        self,
        start_url: str,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        rate: float = 2.0,
        transport: httpx2.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
        site_config: SiteConfig | None = None,
        today: date | None = None,
        rules: list[Rule] | None = None,
        link_check_limit: int = MAX_LINK_CHECKS,
        quick: bool = False,
        progress: Progress | None = None,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        self.start_url = ensure_url(start_url)
        self.max_pages = max_pages
        self.rate = rate
        self.fetcher = Fetcher(rate=rate, transport=transport, clock=clock)
        self.site_config = site_config
        self.today = today or date.today()
        self.rules = rules if rules is not None else all_rules()
        self.link_check_limit = link_check_limit
        # quick = single page: no sitemaps, no link checks, page-scope probes only
        self.quick = quick
        self.progress = progress
        domain = domain_of(self.start_url)
        self.crawl = Crawl(
            crawl_id=f"{domain}/{self.today.isoformat()}",
            domain=domain,
            start_url=self.start_url,
            origin=origin_of(self.start_url),
            host=host_of(self.start_url),
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
            user_agent=USER_AGENT,
            rate=rate,
            max_pages=max_pages,
        )
        self._done: set[str] = set()

    async def run(self) -> Crawl:
        try:
            await self._crawl_pages()
            if not self.fetcher.rate_limited and not self.quick:
                await self._check_links()
            if not self.fetcher.rate_limited:
                await self._probe()
        finally:
            self.crawl.rate_limited = self.fetcher.rate_limited
            await self.fetcher.aclose()
        return self.crawl

    # -- pages -------------------------------------------------------------

    async def _crawl_pages(self) -> None:
        crawl = self.crawl
        start = await self._fetch_page(self.start_url, depth=0, source="start")
        if self.fetcher.rate_limited:
            return
        if start is not None:
            crawl.host = host_of(start.final_url) or crawl.host
            crawl.origin = origin_of(start.final_url)
        robots_file, _ = await self.fetcher.robots(crawl.origin)
        crawl.robots = robots_file
        if not self.quick:
            await self._load_sitemaps()

        links: deque[tuple[str, int | None]] = deque()
        from_sitemap: deque[str] = deque()
        queued: set[str] = set(self._done)
        if start is not None:
            self._enqueue_links(start, links, queued)
        for sitemap in crawl.sitemaps:
            for entry in sitemap.entries if sitemap.kind == "urlset" else []:
                url = normalize(entry.loc)
                if host_of(url) == crawl.host and url not in queued:
                    queued.add(url)
                    from_sitemap.append(url)

        while (links or from_sitemap) and not self.fetcher.rate_limited:
            if len(crawl.pages) >= self.max_pages:
                crawl.capped = True
                break
            if links:
                url, depth = links.popleft()
                source: PageSource = "link"
            else:
                url, depth, source = from_sitemap.popleft(), None, "sitemap"
            if url in self._done:  # already fetched as a redirect target
                continue
            page = await self._fetch_page(url, depth=depth, source=source)
            if page is not None:
                self._enqueue_links(page, links, queued)
            if self.progress is not None:
                await self.progress(len(crawl.pages), self.max_pages, url)

    async def _fetch_page(
        self, url: str, *, depth: int | None, source: PageSource
    ) -> Page | None:
        if not await self.fetcher.allowed(url):
            self._skip(url)
            return None
        result = await self.fetcher.get(url, html_only=True)
        res = result.resource
        page = Page(**res.model_dump(), depth=depth, source=source)
        final = normalize(res.final_url)
        self._done.add(normalize(url))
        if res.robots_blocked:
            self._skip(final)
        first_time = final not in self._done or final == normalize(url)
        self._done.add(final)
        if (
            res.status == 200
            and is_html(res.content_type)
            and result.body
            and first_time
            and (source == "start" or host_of(final) == self.crawl.host)
        ):
            text = result.text()
            page.html = parse_html(text, final)
            if source == "start":
                platform, evidence = detect(text, res.headers)
                self.crawl.platform = platform
                self.crawl.platform_evidence = evidence
        self.crawl.pages.append(page)
        return page

    def _enqueue_links(
        self, page: Page, links: deque[tuple[str, int | None]], queued: set[str]
    ) -> None:
        if page.html is None:
            return
        depth = page.depth + 1 if page.depth is not None else None
        for link in page.html.links:
            url = normalize(link.href)
            if host_of(url) != self.crawl.host or url in queued or url in self._done:
                continue
            queued.add(url)
            links.append((url, depth))

    def _skip(self, url: str) -> None:
        if url not in self.crawl.skipped_robots:
            self.crawl.skipped_robots.append(url)

    # -- sitemaps ----------------------------------------------------------

    async def _load_sitemaps(self) -> None:
        robots_file, parsed = await self.fetcher.robots(self.crawl.origin)
        declared = [s for s in parsed.sitemaps if s.startswith(("http://", "https://"))]
        queue = deque(declared or [f"{self.crawl.origin}/sitemap.xml"])
        seen: set[str] = set()
        while queue and not self.fetcher.rate_limited:
            url = queue.popleft()
            if url in seen:
                continue
            if len(self.crawl.sitemaps) >= MAX_SITEMAP_FILES:
                self.crawl.sitemaps_capped = True
                break
            seen.add(url)
            sitemap = await self._fetch_sitemap(url)
            self.crawl.sitemaps.append(sitemap)
            if sitemap.kind == "index":
                # only absolute child sitemaps on the sitemap's own host
                queue.extend(
                    e.loc
                    for e in sitemap.entries
                    if e.loc.startswith(("http://", "https://"))
                    and host_of(e.loc) == host_of(sitemap.url)
                )

    async def _fetch_sitemap(self, url: str) -> SitemapFile:
        result = await self.fetcher.get(url, max_body=SITEMAP_MAX_BYTES)
        sitemap = SitemapFile(**result.resource.model_dump())
        body = result.body
        if sitemap.status != 200 or not body:
            return sitemap
        if body[:2] == b"\x1f\x8b":
            try:
                inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
                body = inflater.decompress(body, SITEMAP_MAX_BYTES)
            except (zlib.error, gzip.BadGzipFile) as exc:
                sitemap.parse_error = f"gzip: {exc}"
                return sitemap
        sitemap.uncompressed_bytes = len(body)
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            sitemap.parse_error = f"XML: {exc}"
            return sitemap
        tag = _local(root.tag)
        sitemap.kind = (
            "index"
            if tag == "sitemapindex"
            else ("urlset" if tag == "urlset" else "unknown")
        )
        for child in root:
            loc = lastmod = None
            for field in child:
                name = _local(field.tag)
                if name == "loc":
                    loc = (field.text or "").strip()
                elif name == "lastmod":
                    lastmod = (field.text or "").strip() or None
            if loc:
                sitemap.entries.append(SitemapEntry(loc=loc, lastmod=lastmod))
        sitemap.entry_count = len(sitemap.entries)
        return sitemap

    # -- link checks -------------------------------------------------------

    def _link_targets(self) -> Iterable[str]:
        for page in self.crawl.pages:
            if page.html is None:
                continue
            for link in page.html.links:
                yield link.href
            yield from page.html.canonicals
            for alt in page.html.hreflang:
                yield alt.href
        for sitemap in self.crawl.sitemaps:
            if sitemap.kind == "urlset":
                for entry in sitemap.entries:
                    yield entry.loc

    async def _check_links(self) -> None:
        crawled = {normalize(p.url) for p in self.crawl.pages}
        crawled |= {normalize(p.final_url) for p in self.crawl.pages}
        skipped = set(self.crawl.skipped_robots)
        todo: list[str] = []
        for target in self._link_targets():
            url = normalize(target)
            if host_of(url) != self.crawl.host or url in crawled or url in skipped:
                continue
            if url not in todo:
                todo.append(url)
        if len(todo) > self.link_check_limit:
            self.crawl.link_checks_capped = True
        for url in todo[: self.link_check_limit]:
            if self.fetcher.rate_limited:
                break
            if not await self.fetcher.allowed(url):
                self._skip(url)
                self.crawl.link_checks.append(
                    Resource(url=url, final_url=url, robots_blocked=True)
                )
                continue
            result = await self.fetcher.get(url, method="HEAD", read_body=False)
            if result.resource.error or result.resource.status in (405, 501):
                result = await self.fetcher.get(url, read_body=False)
            self.crawl.link_checks.append(result.resource)

    # -- probes ------------------------------------------------------------

    async def _probe(self) -> None:
        ctx = ProbeContext(
            crawl=self.crawl, site_config=self.site_config, token=uuid.uuid4().hex[:12]
        )
        cache: dict[tuple[str, str, str], Probe] = {}
        for rule in self.rules:
            if self.fetcher.rate_limited:
                break
            if rule.probes is None or not rule.applies(self.crawl.platform):
                continue
            if self.quick and rule.scope != "page":
                continue
            try:
                specs = list(rule.probes(ctx))
            except Exception as exc:  # a broken rule must not kill the crawl
                self.crawl.probe_errors.append(
                    f"{rule.id}: {type(exc).__name__}: {exc}"
                )
                continue
            for spec in specs:
                if self.fetcher.rate_limited:
                    break
                key = (normalize(spec.url), spec.kind, spec.method)
                cached = cache.get(key)
                if cached is None:
                    cached = await self._fetch_probe(rule.id, spec)
                    cache[key] = cached
                probe = cached.model_copy(update={"rule_id": rule.id, "key": spec.key})
                self.crawl.probes.append(probe)

    async def _fetch_probe(self, rule_id: str, spec: ProbeSpec) -> Probe:
        url = normalize(spec.url)
        if spec.kind == "image":
            result = await self.fetcher.get(
                url, range_bytes=IMAGE_RANGE_BYTES, max_body=IMAGE_RANGE_BYTES
            )
        elif spec.kind == "status":
            result = await self.fetcher.get(url, method=spec.method, read_body=False)
        elif spec.kind == "text":
            result = await self.fetcher.get(url, max_body=PROBE_TEXT_MAX)
        else:
            result = await self.fetcher.get(url, html_only=True)
        res = result.resource
        probe = Probe(**res.model_dump(), rule_id=rule_id, key=spec.key, kind=spec.kind)
        if res.robots_blocked:
            self._skip(normalize(res.final_url))
        if spec.kind == "image" and result.body:
            size = image_size(result.body)
            if size is not None:
                probe.image_width, probe.image_height = size
        elif spec.kind == "text" and result.body:
            probe.text = decode(result.body, res.content_type)
        elif spec.kind == "page" and result.body and is_html(res.content_type):
            probe.html = parse_html(result.text(), normalize(res.final_url))
        return probe


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


async def crawl_site(
    start_url: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    rate: float = 2.0,
    transport: httpx2.AsyncBaseTransport | None = None,
    clock: Clock | None = None,
    site_config: SiteConfig | None = None,
    today: date | None = None,
    quick: bool = False,
    progress: Progress | None = None,
) -> Crawl:
    crawler = Crawler(
        start_url,
        max_pages=max_pages,
        rate=rate,
        transport=transport,
        clock=clock,
        site_config=site_config,
        today=today,
        quick=quick,
        progress=progress,
    )
    return await crawler.run()
