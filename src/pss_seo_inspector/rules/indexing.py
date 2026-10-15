"""IDX-01..IDX-17: indexing and crawl rules."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import Resource, Severity
from ..urlutil import host_of, normalize, path_of, query_keys, snip, strip_query
from .base import (
    AuditContext,
    Issue,
    Manual,
    Outcome,
    ProbeContext,
    ProbeSpec,
    Rule,
    canonical_is_self,
    is_noindex,
    noindex_evidence,
    status_evidence,
)
from .base import (
    linked_from_note as _from,
)

GOOGLEBOT = "googlebot"
PERMANENT = (301, 308)

UTILITY_PROBE_PATHS: dict[str, list[str]] = {
    "generic": ["/search?q=test", "/cart", "/checkout", "/account", "/login"],
    "shopify": ["/search?q=test", "/cart", "/checkout", "/account", "/account/login"],
    "magento2": [
        "/catalogsearch/result/?q=test",
        "/checkout/cart/",
        "/checkout/",
        "/customer/account/login/",
        "/customer/account/create/",
    ],
    "woocommerce": ["/?s=test", "/cart/", "/checkout/", "/my-account/"],
}
UTILITY_PATH_RE = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:search|cart|checkouts?|account|login|"
    r"my-account|customer/account|catalogsearch|wishlist)(?:/|$)",
    re.IGNORECASE,
)

TRACKING_PARAMS = {
    "gclid",
    "fbclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "_ga",
    "yclid",
    "dclid",
    "igshid",
    "srsltid",
    "_gl",
}
SESSION_PARAMS = {
    "sid",
    "sessionid",
    "session_id",
    "phpsessid",
    "jsessionid",
    "___sid",
    "sessid",
}
PAGINATION_PARAMS = {"page", "p"}
GENERIC_ANCHORS = {
    "click here",
    "here",
    "read more",
    "more",
    "learn more",
    "link",
    "this",
    "click",
    "go",
    "details",
    "more info",
}
SEARCH_BOTS = ("OAI-SearchBot", "Claude-SearchBot", "PerplexityBot")
TRAINING_BOTS = ("GPTBot", "ClaudeBot", "Google-Extended", "CCBot", "Applebot-Extended")
UNSUPPORTED_ROBOTS = {"user-agent", "allow", "disallow", "sitemap"}
ROBOTS_MAX_BYTES = 500 * 1024
SITEMAP_MAX_URLS = 50_000
SITEMAP_MAX_BYTES = 50 * 1024 * 1024


def _blocked_for_google(ctx: AuditContext, url: str) -> bool:
    return not ctx.robots.allowed(GOOGLEBOT, url)


# -- IDX-01 ------------------------------------------------------------------


def _idx01_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    paths = UTILITY_PROBE_PATHS.get(ctx.platform, UTILITY_PROBE_PATHS["generic"])
    extra = ctx.site_config.noindex_urls if ctx.site_config else []
    for path in [*paths, *extra]:
        url = ctx.absolute(path)
        yield ProbeSpec(key=url, url=url, kind="page")


def _idx01(ctx: AuditContext) -> Iterable[Outcome]:
    candidates: list[tuple[str, Resource]] = [(p.url, p) for p in ctx.probes("IDX-01")]
    candidates += [
        (p.final_url, p)
        for p in ctx.html_pages()
        if UTILITY_PATH_RE.match(path_of(p.final_url))
    ]
    seen: set[str] = set()
    for url, res in candidates:
        final = normalize(res.final_url)
        if final in seen:
            continue
        seen.add(final)
        if _blocked_for_google(ctx, url) or _blocked_for_google(ctx, final):
            continue
        if res.robots_blocked:
            yield Manual(f"{url}: blocked for this crawler only; check noindex by hand")
            continue
        if res.error or res.status != 200:
            continue
        if is_noindex(res):
            continue
        yield Issue(
            url,
            f"{status_evidence(res)}; indexable (no noindex) and not blocked by "
            f"robots.txt; {noindex_evidence(res)}",
        )


# -- IDX-02 ------------------------------------------------------------------


def _idx02_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    url = ctx.absolute(f"/pss-404-check-{ctx.token}")
    yield ProbeSpec(key="random", url=url, kind="page")


def _idx02(ctx: AuditContext) -> Iterable[Outcome]:
    probe = ctx.probe("IDX-02", "random")
    if probe is None:
        yield Manual("Random-URL probe was not run")
        return
    if probe.robots_blocked or (probe.error and not probe.chain):
        yield Manual(f"Random-URL probe not fetched: {status_evidence(probe)}")
        return
    evidence = status_evidence(probe)
    if probe.redirected:
        yield Issue(probe.url, f"Non-existent URL redirects (soft 404): {evidence}")
    elif probe.status not in (404, 410):
        yield Issue(probe.url, f"Non-existent URL returns {probe.status}: {evidence}")
    elif probe.html is not None and not is_noindex(probe):
        yield Issue(
            probe.url,
            f"{evidence}; the 404 page has no noindex",
            Severity.low,
        )


# -- IDX-03 ------------------------------------------------------------------


def _acceptable_canonicals(platform: str, url: str) -> list[str]:
    """Canonical targets that are correct for ``url``."""
    parts = urlsplit(url)
    path = parts.path or "/"
    segments = [s for s in path.split("/") if s]
    if platform == "shopify" and len(segments) >= 3 and segments[0] == "collections":
        if segments[2] == "products" and len(segments) >= 4:
            path = f"/products/{segments[3]}"
        elif segments[2] != "products":
            path = f"/collections/{segments[1]}"
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k.lower() in PAGINATION_PARAMS]
    with_page = urlunsplit((parts.scheme, parts.netloc, path, urlencode(kept), ""))
    clean = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return list(dict.fromkeys([normalize(with_page), normalize(clean)]))


def _canonical_problems(platform: str, url: str, page: Resource) -> list[str]:
    html = getattr(page, "html", None)
    if html is None:
        return []
    if not html.canonicals:
        return ["no canonical"]
    problems: list[str] = []
    if len({normalize(c) for c in html.canonicals}) > 1:
        problems.append(f"{len(html.canonicals)} different canonicals")
    raw = html.canonicals_raw[0]
    if not raw.startswith(("http://", "https://")):
        problems.append("canonical is not absolute")
    target = normalize(html.canonicals[0])
    self_url = normalize(url)
    has_variant = urlsplit(self_url).query or _acceptable_canonicals(
        platform, self_url
    )[-1] != strip_query(self_url)
    if has_variant:
        accepted = _acceptable_canonicals(platform, self_url)
        if target not in accepted:
            problems.append(f"variant URL canonical should be {accepted[-1]}")
    elif target != self_url:
        problems.append("canonical is not self-referencing")
    return problems


def _idx03_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    pages = ctx.html_pages()
    if ctx.platform == "shopify":
        product = next(
            (
                p
                for p in pages
                if re.fullmatch(r"/products/[^/]+", path_of(p.final_url))
            ),
            None,
        )
        if product is not None:
            handle = path_of(product.final_url).rsplit("/", 1)[-1]
            for url in (
                ctx.absolute(f"/collections/all/products/{handle}"),
                strip_query(product.final_url) + "?variant=1",
            ):
                yield ProbeSpec(key=url, url=url)
        collection = next(
            (
                p
                for p in pages
                if re.fullmatch(r"/collections/[^/]+", path_of(p.final_url))
                and path_of(p.final_url).rsplit("/", 1)[-1]
                not in ("all", "vendors", "types")
            ),
            None,
        )
        if collection is not None:
            base = strip_query(collection.final_url)
            for suffix in (
                "?page=2",
                "?sort_by=price-ascending",
                "?filter.v.availability=1",
            ):
                yield ProbeSpec(key=base + suffix, url=base + suffix)
    elif ctx.platform == "magento2":
        category = next(
            (
                p
                for p in pages
                if p.html is not None and "catalog-category-view" in p.html.body_classes
            ),
            None,
        )
        if category is not None:
            base = strip_query(category.final_url)
            for suffix in (
                "?p=2",
                "?product_list_order=price",
                "?product_list_limit=36",
            ):
                yield ProbeSpec(key=base + suffix, url=base + suffix)


def _idx03(ctx: AuditContext) -> Iterable[Outcome]:
    targets: list[tuple[str, Resource]] = [
        (p.final_url, p) for p in ctx.indexable_pages()
    ]
    targets += [
        (p.final_url, p)
        for p in ctx.probes("IDX-03")
        if p.status == 200 and p.html is not None and not is_noindex(p)
    ]
    seen: set[str] = set()
    for url, res in targets:
        if url in seen:
            continue
        seen.add(url)
        problems = _canonical_problems(ctx.platform, url, res)
        if problems:
            html = getattr(res, "html", None)
            snippet = (
                html.canonical_snippets[0] if html and html.canonical_snippets else ""
            )
            yield Issue(
                url, f"{'; '.join(problems)}: {snippet or 'no <link rel=canonical>'}"
            )


# -- IDX-04 ------------------------------------------------------------------


def _idx04(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.indexable_pages():
        assert page.html is not None
        bad: list[str] = []
        for link in page.html.links:
            if not ctx.is_internal(link.href):
                continue
            target = ctx.resource(link.href)
            if target is None or target.error:
                continue
            reason = ""
            if target.redirected:
                reason = f"redirects to {target.final_url}"
            else:
                tpage = ctx.page(link.href)
                if tpage is not None and not canonical_is_self(tpage):
                    assert tpage.html is not None
                    reason = f"canonical is {tpage.html.canonicals[0]}"
            if reason:
                bad.append(f"{link.raw} ({reason})")
        unique = list(dict.fromkeys(bad))
        if unique:
            yield Issue(
                page.final_url,
                f"{len(unique)} internal links to non-canonical URLs, e.g. "
                + "; ".join(unique[:3]),
            )


# -- IDX-05 ------------------------------------------------------------------


def _link_problem(res: Resource) -> str | None:
    if res.robots_blocked:
        return None
    if res.error:
        return f"error ({res.error})"
    if res.status is not None and res.status >= 400:
        return f"HTTP {res.status}"
    redirects = len(res.chain) - 1
    if redirects > 1:
        return f"redirect chain of {redirects} hops"
    if redirects == 1 and res.chain[0].status not in PERMANENT:
        return f"temporary redirect ({res.chain[0].status})"
    return None


def _idx05(ctx: AuditContext) -> Iterable[Outcome]:
    unchecked = 0
    skipped = set(ctx.crawl.skipped_robots)
    for target in ctx.linked_from:
        res = ctx.resource(target)
        if res is None:
            unchecked += target not in skipped
            continue
        if res.error and res.error.startswith("read-only policy:"):
            yield Manual(
                f"{target}: {res.error}; destination not requested: {res.final_url}"
            )
            continue
        problem = _link_problem(res)
        if problem:
            yield Issue(
                target,
                f"{problem}: {status_evidence(res)}{_from(ctx, target)}",
            )
    if unchecked or ctx.crawl.link_checks_capped:
        yield Manual(
            f"{unchecked} internal link targets were not checked (link-check limit "
            "of 500 or max_pages reached)"
        )


# -- IDX-06 ------------------------------------------------------------------


def _hygiene(ctx: AuditContext, url: str) -> list[str]:
    parts = urlsplit(url)
    path = parts.path
    problems: list[str] = []
    if "//" in path:
        problems.append("double slash in path")
    if any(c.isupper() for c in re.sub(r"%[0-9A-Fa-f]{2}", "", path)):
        problems.append("uppercase letters in path")
    keys = [k.lower() for k in query_keys(url)]
    tracking = [k for k in keys if k.startswith("utm_") or k in TRACKING_PARAMS]
    if tracking:
        problems.append(f"tracking parameter {', '.join(tracking)}")
    session = [k for k in keys if k in SESSION_PARAMS]
    if session or "/referer/" in path.lower() or ";jsessionid=" in path.lower():
        problems.append("session/referer parameter")
    res = ctx.resource(url)
    if res is not None:
        if path.lower().endswith(".html") and res.status == 404:
            problems.append("legacy .html URL returns 404")
        toggled = normalize(
            urlunsplit(
                (
                    parts.scheme,
                    parts.netloc,
                    path[:-1] if path.endswith("/") and path != "/" else path + "/",
                    parts.query,
                    "",
                )
            )
        )
        page = ctx.page(url)
        canonical = (
            normalize(page.html.canonicals[0])
            if page is not None and page.html is not None and page.html.canonicals
            else None
        )
        if normalize(res.final_url) == toggled or canonical == toggled:
            problems.append("trailing slash does not match the canonical form")
    return problems


def _idx06(ctx: AuditContext) -> Iterable[Outcome]:
    seen: set[str] = set()
    for page in ctx.html_pages():
        assert page.html is not None
        # <a href> only: CDN image names and file paths are not page URLs.
        for url, snippet in ((link.href, link.snippet) for link in page.html.links):
            if not ctx.is_internal(url) or url in seen:
                continue
            seen.add(url)
            problems = _hygiene(ctx, url)
            if problems:
                yield Issue(
                    url,
                    f"{'; '.join(problems)}; on {page.final_url}: {snippet}",
                )


# -- IDX-07 ------------------------------------------------------------------


def _idx07(ctx: AuditContext) -> Iterable[Outcome]:
    home = normalize(ctx.origin + "/")
    linked = set(ctx.linked_from)
    linked |= {
        normalize(p.final_url) for p in ctx.crawl.pages if normalize(p.url) in linked
    }
    note = " (crawl hit max_pages; may be a false positive)" if ctx.crawl.capped else ""
    crawled = len(ctx.html_pages())
    for url in ctx.sitemap_urls:
        if url == home or not ctx.is_internal(url) or url in linked:
            continue
        yield Issue(
            url, f"In sitemap, not linked from any of {crawled} crawled pages{note}"
        )


# -- IDX-08 ------------------------------------------------------------------


def _idx08(ctx: AuditContext) -> Iterable[Outcome]:
    unchecked = 0
    for url in ctx.sitemap_urls:
        if not ctx.is_internal(url):
            continue  # foreign-host entries are reported by IDX-13
        if _blocked_for_google(ctx, url):
            yield Issue(url, "In sitemap but blocked by robots.txt for Googlebot")
            continue
        res = ctx.resource(url)
        if res is None:
            unchecked += url not in ctx.crawl.skipped_robots
            continue
        if res.robots_blocked:
            continue
        evidence = status_evidence(res)
        if res.error or (res.status is not None and res.status >= 400):
            yield Issue(url, f"In sitemap but not 200: {evidence}")
        elif res.redirected:
            yield Issue(url, f"In sitemap but redirects: {evidence}")
        elif is_noindex(res):
            yield Issue(url, f"In sitemap but noindex: {noindex_evidence(res)}")
        else:
            page = ctx.page(url)
            if page is not None and not canonical_is_self(page):
                assert page.html is not None
                yield Issue(
                    url,
                    f"In sitemap but canonical points elsewhere: "
                    f"{page.html.canonical_snippets[0]}",
                )
    if unchecked:
        yield Manual(f"{unchecked} sitemap URLs were not checked (limit)")


# -- IDX-09 ------------------------------------------------------------------


def _idx09_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    host = ctx.crawl.host
    bare = host[4:] if host.startswith("www.") else host
    canonical = normalize(ctx.crawl.origin + "/")
    for scheme in ("https", "http"):
        for name in (bare, f"www.{bare}"):
            url = normalize(f"{scheme}://{name}/")
            if url != canonical:
                yield ProbeSpec(key=url, url=url, kind="status")


def _idx09(ctx: AuditContext) -> Iterable[Outcome]:
    canonical = normalize(ctx.origin + "/")
    for probe in ctx.probes("IDX-09"):
        if probe.error and not probe.chain:
            continue  # host does not resolve / no listener: nothing to index
        if probe.robots_blocked:
            yield Manual(f"{probe.url}: blocked by robots.txt for this crawler")
            continue
        evidence = status_evidence(probe)
        if not probe.redirected:
            yield Issue(probe.url, f"Variant host does not redirect: {evidence}")
            continue
        if normalize(probe.final_url) != canonical or probe.status != 200:
            yield Issue(
                probe.url, f"Variant does not end at {canonical} (200): {evidence}"
            )
            continue
        temporary = [h.status for h in probe.chain[:-1] if h.status not in PERMANENT]
        if temporary:
            yield Issue(probe.url, f"Non-permanent redirect {temporary}: {evidence}")
        elif len(probe.chain) - 1 > 1:
            yield Issue(
                probe.url,
                f"Redirect chain of {len(probe.chain) - 1} hops (expected one): "
                f"{evidence}",
            )


# -- IDX-10 ------------------------------------------------------------------


def _idx10_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    for entry in ctx.site_config.legacy_urls if ctx.site_config else []:
        if not entry.strip():
            continue
        url = ctx.absolute(entry.strip())
        yield ProbeSpec(key=url, url=url, kind="status")


def _idx10(ctx: AuditContext) -> Iterable[Outcome]:
    for probe in ctx.probes("IDX-10"):
        if probe.robots_blocked:
            yield Manual(f"{probe.url}: blocked by robots.txt for this crawler")
        elif probe.error or probe.status != 200:
            yield Issue(
                probe.key,
                f"Legacy URL does not reach a 200 page: {status_evidence(probe)}",
            )


# -- IDX-11 ------------------------------------------------------------------


def _idx11(ctx: AuditContext) -> Iterable[Outcome]:
    robots = ctx.crawl.robots
    if robots is None:
        yield Manual("robots.txt was not fetched")
        return
    url = robots.url
    if robots.error:
        yield Issue(url, f"robots.txt unreachable: {status_evidence(robots)}")
        return
    if robots.status is not None and (robots.status >= 500 or robots.status == 429):
        yield Issue(
            url,
            f"robots.txt returns {robots.status}; Google treats the site as fully "
            f"disallowed while this lasts: {status_evidence(robots)}",
        )
        return
    if robots.status in (401, 403):
        yield Issue(
            url,
            f"robots.txt returns {robots.status}; Google treats this like a 404 "
            f"(no robots.txt, everything allowed): {status_evidence(robots)}",
            Severity.low,
        )
        return
    if robots.status != 200:
        return
    if robots.size_bytes > ROBOTS_MAX_BYTES:
        yield Issue(
            url,
            f"robots.txt is {robots.size_bytes} bytes; Google ignores "
            "everything after 500 KiB",
        )
    if not robots.utf8_ok:
        yield Issue(url, "robots.txt is not valid UTF-8")
    for number, raw in enumerate(robots.text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        name, value = (p.strip() for p in line.split(":", 1))
        key = name.lower()
        if key not in UNSUPPORTED_ROBOTS:
            yield Issue(
                url,
                f"line {number}: '{snip(line, 120)}' is not supported by "
                "Google and is ignored",
            )
        elif key == "sitemap" and not value.startswith(("http://", "https://")):
            yield Issue(
                url,
                f"line {number}: Sitemap must be an absolute URL: '{snip(line, 120)}'",
            )


# -- IDX-12 ------------------------------------------------------------------


def _idx12(ctx: AuditContext) -> Iterable[Outcome]:
    resources: list[Resource] = [
        *ctx.crawl.pages,
        *ctx.crawl.link_checks,
        *ctx.crawl.probes,
    ]
    seen: set[str] = set()
    for res in resources:
        url = normalize(res.final_url)
        if url in seen or res.robots_blocked or not ctx.is_internal(url):
            continue
        seen.add(url)
        if is_noindex(res) and _blocked_for_google(ctx, url):
            yield Issue(
                url,
                f"noindex is never seen: robots.txt blocks Googlebot; "
                f"{noindex_evidence(res)}",
            )
    wanted = set(ctx.linked_from) | set(ctx.sitemap_urls)
    unverified = [
        u for u in ctx.crawl.skipped_robots if ctx.is_internal(u) and u in wanted
    ]
    if unverified:
        more = f" +{len(unverified) - 5} more" if len(unverified) > 5 else ""
        yield Manual(
            f"{len(unverified)} linked/sitemap URLs are robots-disallowed, so their "
            f"noindex cannot be verified: {', '.join(unverified[:5])}{more}"
        )


# -- IDX-13 ------------------------------------------------------------------


def _parse_lastmod(value: str) -> datetime | None:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _idx13(ctx: AuditContext) -> Iterable[Outcome]:
    if not ctx.crawl.sitemaps:
        yield Issue(ctx.origin + "/sitemap.xml", "No sitemap was found")
        return
    if ctx.crawl.sitemaps_capped:
        yield Manual("Sitemap file limit (50) reached; remaining sitemaps not checked")
    try:
        now = datetime.fromisoformat(ctx.crawl.fetched_at)
    except ValueError:
        now = datetime.now(UTC)
    for sm in ctx.crawl.sitemaps:
        if sm.robots_blocked:
            yield Issue(sm.url, "Sitemap is blocked by robots.txt")
            continue
        if sm.error or sm.status != 200:
            yield Issue(sm.url, f"Sitemap not reachable: {status_evidence(sm)}")
            continue
        if sm.parse_error:
            yield Issue(sm.url, f"Sitemap cannot be parsed: {sm.parse_error}")
            continue
        if sm.kind == "unknown":
            yield Issue(sm.url, "Root element is neither <urlset> nor <sitemapindex>")
            continue
        if sm.entry_count > SITEMAP_MAX_URLS:
            yield Issue(sm.url, f"{sm.entry_count} URLs (max 50,000 per file)")
        if sm.uncompressed_bytes > SITEMAP_MAX_BYTES or sm.truncated:
            yield Issue(
                sm.url, f"{sm.uncompressed_bytes} bytes uncompressed (max 50 MB)"
            )
        relative = [
            e.loc for e in sm.entries if not e.loc.startswith(("http://", "https://"))
        ]
        if relative:
            yield Issue(
                sm.url, f"{len(relative)} non-absolute <loc>, e.g. {relative[0]}"
            )
        if sm.kind != "urlset":
            continue
        foreign = [
            e.loc
            for e in sm.entries
            if e.loc.startswith("http") and host_of(e.loc) != host_of(sm.url)
        ]
        if foreign:
            yield Issue(
                sm.url, f"{len(foreign)} URLs on another host, e.g. {foreign[0]}"
            )
        missing = [e.loc for e in sm.entries if not e.lastmod]
        if missing:
            yield Issue(
                sm.url,
                f"{len(missing)} of {sm.entry_count} URLs have no <lastmod>, "
                f"e.g. {missing[0]}",
            )
        future = [
            e
            for e in sm.entries
            if e.lastmod
            and (d := _parse_lastmod(e.lastmod)) is not None
            and d > now + timedelta(days=1)
        ]
        if future:
            yield Issue(
                sm.url,
                f"{len(future)} <lastmod> dates in the future, e.g. "
                f"{future[0].loc} {future[0].lastmod}",
            )


# -- IDX-14 ------------------------------------------------------------------


_LINK_CANONICAL_RE = re.compile(r"<([^>]+)>\s*;[^,]*rel=\"?canonical\"?", re.IGNORECASE)


def _idx14(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.indexable_pages():
        assert page.html is not None
        canonicals = page.html.canonicals
        header = page.headers.get("link", "")
        match = _LINK_CANONICAL_RE.search(header)
        if (
            match
            and canonicals
            and normalize(match.group(1)) != normalize(canonicals[0])
        ):
            yield Issue(
                page.final_url,
                f"Link header canonical {match.group(1)} differs from HTML "
                f"{page.html.canonical_snippets[0]}",
            )
        if not canonicals or canonical_is_self(page):
            continue
        target = normalize(canonicals[0])
        res = ctx.resource(target)
        snippet = page.html.canonical_snippets[0]
        if res is None or res.robots_blocked:
            continue
        if res.error or (res.status is not None and res.status >= 400):
            yield Issue(
                page.final_url,
                f"Canonical target not 200 ({status_evidence(res)}): {snippet}",
            )
        elif res.redirected:
            yield Issue(
                page.final_url,
                f"Canonical target redirects ({status_evidence(res)}): {snippet}",
            )
        elif is_noindex(res):
            yield Issue(
                page.final_url,
                f"Canonical target is noindex ({noindex_evidence(res)}): {snippet}",
            )
        else:
            tpage = ctx.page(target)
            if (
                tpage is not None
                and tpage.html is not None
                and not canonical_is_self(tpage)
            ):
                yield Issue(
                    page.final_url,
                    f"Canonical chain/loop: target canonicalizes to "
                    f"{tpage.html.canonicals[0]}: {snippet}",
                )


# -- IDX-15 ------------------------------------------------------------------


def _idx15(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.html_pages():
        assert page.html is not None
        if not page.final_url.startswith("https://"):
            continue
        insecure = [r for r in page.html.resources if r.startswith("http://")]
        links = [
            link.raw
            for link in page.html.links
            if link.href.startswith("http://") and ctx.is_internal(link.href)
        ]
        if insecure:
            yield Issue(
                page.final_url,
                f"{len(insecure)} http:// resources on an https "
                f"page, e.g. {', '.join(insecure[:3])}",
            )
        if links:
            unique = list(dict.fromkeys(links))
            yield Issue(
                page.final_url,
                f"{len(unique)} internal http:// links, e.g. {', '.join(unique[:3])}",
            )


# -- IDX-16 ------------------------------------------------------------------


def _idx16(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.html_pages():
        assert page.html is not None
        internal = [link for link in page.html.links if ctx.is_internal(link.href)]
        nofollow = [link.snippet for link in internal if "nofollow" in link.rel]
        empty = [link.snippet for link in internal if not link.text.strip()]
        generic = [
            link.snippet
            for link in internal
            if link.text.strip().lower() in GENERIC_ANCHORS
        ]
        for label, items in (
            ("internal links with rel=nofollow", nofollow),
            ("internal links with empty anchor text", empty),
            ("internal links with generic anchor text", generic),
        ):
            if items:
                yield Issue(page.final_url, f"{len(items)} {label}, e.g. {items[0]}")
        if page.depth is not None and page.depth > 3:
            yield Issue(
                page.final_url, f"Page is {page.depth} clicks from the home page"
            )


# -- IDX-17 ------------------------------------------------------------------


def _idx17_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    url = ctx.absolute("/llms.txt")
    yield ProbeSpec(key="llms.txt", url=url, kind="text")


def _idx17(ctx: AuditContext) -> Iterable[Outcome]:
    robots_url = (
        ctx.crawl.robots.url if ctx.crawl.robots else ctx.origin + "/robots.txt"
    )
    home = ctx.origin + "/"
    parsed = ctx.robots.parsed
    for bot in SEARCH_BOTS:
        if not ctx.robots.allowed(bot.lower(), home):
            group = (
                f"its own group '{bot}'" if parsed.has_group(bot) else "the '*' group"
            )
            yield Issue(
                robots_url,
                f"AI search bot {bot} is blocked from / by {group}; the site will "
                "not appear in that assistant's search answers",
            )
    blocked = [b for b in TRAINING_BOTS if not ctx.robots.allowed(b.lower(), home)]
    if blocked:
        policy = ", ".join(
            f"{b}: {'blocked' if b in blocked else 'allowed'}" for b in TRAINING_BOTS
        )
        yield Issue(robots_url, f"AI training-bot policy: {policy}", Severity.info)
    llms = ctx.probe("IDX-17", "llms.txt")
    if llms is not None and not llms.robots_blocked and llms.status != 200:
        yield Issue(
            llms.url,
            f"No llms.txt ({status_evidence(llms)}); optional, "
            "Google Search does not use it",
            Severity.info,
        )


RULES = [
    Rule(
        id="IDX-01",
        category="indexing",
        severity=Severity.high,
        description="Utility pages (search, cart, checkout, account, configured URLs) "
        "are noindex or blocked by robots.txt",
        check=_idx01,
        probes=_idx01_probes,
        manual="Confirm noindex is in the server HTML, not added or removed by "
        "JavaScript (needs a rendered check).",
        fix={
            "generic": 'Add <meta name="robots" content="noindex"> to these '
            "templates or disallow them in robots.txt.",
            "shopify": 'In theme.liquid <head>, output <meta name="robots" '
            "content=\"noindex\"> for template 'search', 'cart' and customer/* "
            "templates; keep Shopify's default robots.txt disallows.",
            "magento2": "Disallow /checkout/, /customer/ and /catalogsearch/ in "
            "Content > Design > Configuration > Search Engine Robots, or set "
            "NOINDEX,FOLLOW for those layouts.",
            "woocommerce": "Mark cart, checkout and my-account as noindex in your "
            "SEO plugin (Yoast/Rank Math) and disallow /?s= in robots.txt.",
        },
    ),
    Rule(
        id="IDX-02",
        category="indexing",
        severity=Severity.critical,
        description="A random non-existent URL returns a real 404 and the 404 page "
        "is noindex",
        check=_idx02,
        probes=_idx02_probes,
        manual="Check Google Search Console > Pages > 'Soft 404' for other "
        "affected URLs.",
        fix={
            "generic": "Return HTTP 404 (not 200 or a redirect) for unknown URLs and "
            "add noindex to the 404 template.",
            "shopify": "Shopify returns 404 for unknown URLs by default; remove any "
            "app or theme redirect for 404s (e.g. a redirect-404-to-home app) and add "
            "noindex for template '404' in theme.liquid.",
            "magento2": "Disable any 404-to-home redirect module and make the "
            "'no-route' CMS page return 404 with NOINDEX.",
            "woocommerce": "Remove 404-redirect plugins; let 404.php return 404 and "
            "add noindex in the SEO plugin.",
        },
    ),
    Rule(
        id="IDX-03",
        category="indexing",
        severity=Severity.high,
        description="Every indexable page has exactly one absolute, self-referencing "
        "canonical; parameter and duplicate URLs canonicalize to the clean URL",
        check=_idx03,
        probes=_idx03_probes,
        scope="page",
        fix={
            "generic": 'Output exactly one <link rel="canonical"> with an absolute '
            "URL: self for normal pages, the clean URL for parameter/duplicate "
            "variants.",
            "shopify": 'In theme.liquid use <link rel="canonical" href="{{ '
            'canonical_url }}"> once; remove duplicate canonicals from apps/sections.',
            "magento2": "Stores > Configuration > Catalog > Catalog > Search Engine "
            "Optimization: set 'Use Canonical Link Meta Tag' for categories and "
            "products to Yes.",
            "woocommerce": "Let the SEO plugin output the canonical; remove theme "
            "duplicates.",
        },
    ),
    Rule(
        id="IDX-04",
        category="indexing",
        severity=Severity.medium,
        description="Internal links point to canonical URLs (count per page)",
        check=_idx04,
        scope="page",
        fix={
            "generic": "Change internal links to the final canonical URL (no "
            "redirects, no parameter variants).",
            "shopify": "Link to /products/{handle} instead of "
            "/collections/{c}/products/{handle}: in product card snippets use "
            "{{ product.url }} without '| within: collection'.",
            "magento2": "Set 'Use Categories Path for Product URLs' to No so links "
            "match the product canonical.",
        },
    ),
    Rule(
        id="IDX-05",
        category="indexing",
        severity=Severity.high,
        description="Every internal link returns 200 directly or via one 301 (no "
        "4xx/5xx, chains or loops)",
        check=_idx05,
        fix={
            "generic": "Update the link to the final URL; fix or redirect (301) "
            "broken targets.",
            "shopify": "Edit the menu (Online Store > Navigation) or section links; "
            "add 301s in Online Store > Navigation > URL Redirects.",
            "magento2": "Fix links in CMS blocks/menus; add 301s in Marketing > SEO & "
            "Search > URL Rewrites.",
            "woocommerce": "Fix links in menus/content; add 301s with a redirect "
            "plugin.",
        },
    ),
    Rule(
        id="IDX-06",
        category="indexing",
        severity=Severity.medium,
        description="URL hygiene in href/src: double slashes, trailing-slash "
        "mismatch, legacy .html 404s, uppercase, tracking and session parameters",
        check=_idx06,
        scope="page",
        fix={
            "generic": "Link to clean lower-case URLs in the canonical form, without "
            "tracking/session parameters.",
            "shopify": "Remove UTM parameters from theme/menu links; fix hard-coded "
            "URLs in sections and snippets.",
            "magento2": "Disable 'Use SID on Storefront' (Stores > Configuration > "
            "General > Web > Session Validation) and fix referer login links.",
        },
    ),
    Rule(
        id="IDX-07",
        category="indexing",
        severity=Severity.medium,
        description="Orphan pages: sitemap URLs that no crawled page links to",
        check=_idx07,
        fix={
            "generic": "Link each sitemap URL from a relevant page or menu, or remove "
            "it from the sitemap if it should not be indexed.",
            "shopify": "Add the page/product to a menu or collection; unpublish "
            "pages that should not exist.",
        },
    ),
    Rule(
        id="IDX-08",
        category="indexing",
        severity=Severity.high,
        description="Sitemap lists only 200, non-redirected, indexable, canonical "
        "URLs that robots.txt allows",
        check=_idx08,
        manual="Google Search Console > Sitemaps: confirm the sitemap is submitted "
        "and processed without errors.",
        fix={
            "generic": "Remove non-200, redirected, noindex, non-canonical and "
            "robots-blocked URLs from the sitemap.",
            "shopify": "Shopify generates the sitemap; unpublish or delete the "
            "resource, or hide it with the seo.hidden metafield.",
            "magento2": "Exclude the URLs in Marketing > SEO & Search > Site Map or "
            "fix their status/canonical.",
            "woocommerce": "Exclude the URLs in the SEO plugin's sitemap settings.",
        },
    ),
    Rule(
        id="IDX-09",
        category="indexing",
        severity=Severity.critical,
        description="Host and protocol variants (www/non-www, http/https) 301 to one "
        "canonical host",
        check=_idx09,
        probes=_idx09_probes,
        fix={
            "generic": "301-redirect every host/protocol variant to the canonical "
            "https host in one hop.",
            "shopify": "Settings > Domains: set the primary domain and enable "
            "'Redirect all traffic to this domain'.",
            "magento2": "Set Base URLs (Stores > Configuration > General > Web) to "
            "https and add server-level 301s for the other host.",
        },
    ),
    Rule(
        id="IDX-10",
        category="indexing",
        severity=Severity.medium,
        description="Legacy domains/URLs from the site config redirect to live 200 "
        "pages",
        check=_idx10,
        probes=_idx10_probes,
        fix={
            "generic": "301 each legacy URL to the closest live page (not the home "
            "page, not a 404).",
            "shopify": "Add the paths in Online Store > Navigation > URL Redirects; "
            "connect legacy domains in Settings > Domains.",
            "magento2": "Add 301s in Marketing > SEO & Search > URL Rewrites.",
        },
    ),
    Rule(
        id="IDX-11",
        category="indexing",
        severity=Severity.high,
        description="robots.txt health: reachable (2xx/4xx, not 5xx/401/403), "
        "≤500 KiB, UTF-8, no unsupported directives, absolute Sitemap lines",
        check=_idx11,
        fix={
            "generic": "Serve robots.txt with 200, UTF-8, under 500 KiB; drop "
            "noindex/crawl-delay lines and use absolute Sitemap URLs.",
            "shopify": "Edit templates/robots.txt.liquid (Online Store > Themes > Edit "
            "code); keep the default groups loop.",
            "magento2": "Content > Design > Configuration > Search Engine Robots.",
        },
    ),
    Rule(
        id="IDX-12",
        category="indexing",
        severity=Severity.high,
        description="No URL is both robots-disallowed and noindex (the noindex is "
        "never seen)",
        check=_idx12,
        fix={
            "generic": "Pick one: to deindex, allow crawling so Google sees the "
            "noindex; block in robots.txt only after it has dropped out.",
            "shopify": "Remove the Disallow for these paths in robots.txt.liquid "
            "while the noindex should take effect.",
        },
    ),
    Rule(
        id="IDX-13",
        category="indexing",
        severity=Severity.medium,
        description="Sitemap format: reachable, ≤50,000 URLs and ≤50 MB per file, "
        "absolute in-scope URLs, lastmod present and not in the future",
        check=_idx13,
        fix={
            "generic": "Split large sitemaps, use absolute same-host URLs and "
            "accurate <lastmod> dates.",
            "shopify": "Shopify generates /sitemap.xml; out-of-scope or future "
            "dates usually come from apps — check app-generated sitemaps.",
            "magento2": "Marketing > SEO & Search > Site Map; Stores > Configuration "
            "> Catalog > XML Sitemap (limits, lastmod).",
            "woocommerce": "Regenerate the sitemap in the SEO plugin.",
        },
    ),
    Rule(
        id="IDX-14",
        category="indexing",
        severity=Severity.high,
        description="Canonical targets are 200, not redirected, indexable and not "
        "chained; HTML and Link-header canonicals agree",
        check=_idx14,
        scope="page",
        fix={
            "generic": "Point canonicals directly at the final, indexable 200 URL.",
            "shopify": "Check apps or theme code that override canonical_url; "
            "update links after handle changes.",
            "magento2": "Regenerate URL rewrites after URL key changes; fix the "
            "canonical module configuration.",
        },
    ),
    Rule(
        id="IDX-15",
        category="indexing",
        severity=Severity.medium,
        description="No mixed content: http resources or internal http links on "
        "https pages",
        check=_idx15,
        scope="page",
        fix={
            "generic": "Change http:// resource and link URLs to https:// (or "
            "protocol-relative).",
            "shopify": "Search theme files, page content and app embeds for "
            "hard-coded http:// URLs.",
            "magento2": "Set secure base URLs and 'Use Secure URLs on Storefront' to "
            "Yes; fix CMS content.",
        },
    ),
    Rule(
        id="IDX-16",
        category="indexing",
        severity=Severity.low,
        description="Internal links: no nofollow, no empty/generic anchor text, pages "
        "within 3 clicks of home",
        check=_idx16,
        scope="page",
        fix={
            "generic": "Remove rel=nofollow from internal links, give links "
            "descriptive text or aria-label, and link deep pages from hubs.",
            "shopify": "Add aria-label to icon links in header/footer sections; "
            "link deep products from collections or menus.",
        },
    ),
    Rule(
        id="IDX-17",
        category="indexing",
        severity=Severity.medium,
        description="AI bots: search bots (OAI-SearchBot, Claude-SearchBot, "
        "PerplexityBot) not blocked; training-bot policy and llms.txt (info)",
        check=_idx17,
        probes=_idx17_probes,
        fix={
            "generic": "Allow AI search bots in robots.txt; block training bots only "
            "if that is a deliberate policy.",
            "shopify": "Edit templates/robots.txt.liquid to add or remove groups for "
            "these user agents.",
            "magento2": "Content > Design > Configuration > Search Engine Robots.",
        },
    ),
]
