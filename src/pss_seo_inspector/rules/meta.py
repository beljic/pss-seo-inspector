"""META-01..META-09: titles, descriptions, headings, social and head hygiene."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from ..models import Page, Severity
from ..urlutil import absolutize, normalize, path_of, snip
from .base import (
    AuditContext,
    Issue,
    Manual,
    Outcome,
    ProbeContext,
    ProbeSpec,
    Rule,
    status_evidence,
)

TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 70, 160
OG_IMAGE_MIN_WIDTH = 1200
MAX_OG_IMAGE_PROBES = 50
TITLE_SEPARATORS = re.compile(r"\s+[|\-–—:·•]\s+")
LOCALE_SEGMENT = re.compile(r"^[a-z]{2}(?:-[a-z]{2,4})?$", re.IGNORECASE)
HREFLANG_CODE = re.compile(
    r"^(?:x-default|[a-z]{2,3}(?:-[a-z]{4})?(?:-(?:[a-z]{2}|\d{3}))?)$", re.IGNORECASE
)
ROBOTS_VALID = {
    "all",
    "noindex",
    "nofollow",
    "none",
    "nosnippet",
    "indexifembedded",
    "notranslate",
    "noimageindex",
    "index",
    "follow",
}
ROBOTS_IGNORED = {"noarchive", "nocache", "noodp", "noydir"}
PARAM_DIRECTIVES = {
    "max-snippet",
    "max-image-preview",
    "max-video-preview",
    "unavailable_after",
}


def _clusters(pages: list[Page], value: dict[str, str]) -> Iterable[list[Page]]:
    groups: dict[str, list[Page]] = defaultdict(list)
    for page in pages:
        text = value.get(page.final_url, "").strip()
        if text:
            groups[text.lower()].append(page)
    return (group for group in groups.values() if len(group) > 1)


def _cluster_evidence(label: str, group: list[Page], snippet: str) -> str:
    urls = ", ".join(p.final_url for p in group[1:4])
    more = f" +{len(group) - 4} more" if len(group) > 4 else ""
    return f"Same {label} on {len(group)} pages (also {urls}{more}): {snippet}"


# -- META-01 -----------------------------------------------------------------


def _meta01(ctx: AuditContext) -> Iterable[Outcome]:
    pages = ctx.canonical_pages()
    config = ctx.site_config
    titles: dict[str, str] = {}
    for page in pages:
        assert page.html is not None
        title = page.html.titles[0].strip() if page.html.titles else ""
        snippet = page.html.title_snippet
        if not title:
            yield Issue(
                page.final_url, f"Missing or empty <title>: {snippet or 'none'}"
            )
            continue
        titles[page.final_url] = title
        if not TITLE_MIN <= len(title) <= TITLE_MAX:
            yield Issue(
                page.final_url,
                f"Title is {len(title)} chars ({TITLE_MIN}–{TITLE_MAX}): {snippet}",
                Severity.medium,
            )
        segments = [s.strip().lower() for s in TITLE_SEPARATORS.split(title)]
        repeated = {s for s in segments if s and segments.count(s) > 1}
        if repeated:
            yield Issue(
                page.final_url,
                f"Repeated title segment '{sorted(repeated)[0]}': {snippet}",
            )
        if config is None:
            continue
        for wrong in config.brand_wrong_spellings:
            if wrong in title:
                yield Issue(
                    page.final_url, f"Wrong brand spelling '{wrong}': {snippet}"
                )
        if config.brand:
            count = title.count(config.brand)
            if count == 0:
                yield Issue(
                    page.final_url, f"Brand '{config.brand}' missing: {snippet}"
                )
            elif count > 1 and not repeated:
                yield Issue(
                    page.final_url,
                    f"Brand '{config.brand}' appears {count} times: {snippet}",
                )
    for group in _clusters(pages, titles):
        first = group[0]
        assert first.html is not None
        yield Issue(
            first.final_url, _cluster_evidence("title", group, first.html.title_snippet)
        )


# -- META-02 -----------------------------------------------------------------


def _meta02(ctx: AuditContext) -> Iterable[Outcome]:
    pages = ctx.canonical_pages()
    descriptions: dict[str, str] = {}
    for page in pages:
        assert page.html is not None
        values = page.html.meta_descriptions
        snippet = page.html.meta_description_snippet
        desc = values[0].strip() if values else ""
        if not desc:
            yield Issue(
                page.final_url,
                "Missing or empty meta description: "
                f"{snippet or 'no <meta name=description>'}",
            )
            continue
        descriptions[page.final_url] = desc
        if len(values) > 1:
            yield Issue(
                page.final_url, f"{len(values)} meta description tags: {snippet}"
            )
        if not DESC_MIN <= len(desc) <= DESC_MAX:
            yield Issue(
                page.final_url,
                f"Meta description is {len(desc)} chars "
                f"({DESC_MIN}–{DESC_MAX}): {snippet}",
            )
    for group in _clusters(pages, descriptions):
        first = group[0]
        assert first.html is not None
        yield Issue(
            first.final_url,
            _cluster_evidence(
                "meta description", group, first.html.meta_description_snippet
            ),
        )


# -- META-03 -----------------------------------------------------------------


def _meta03(ctx: AuditContext) -> Iterable[Outcome]:
    config = ctx.site_config
    if config is None or not config.product_name_pattern:
        return
    pattern = re.compile(config.product_name_pattern)
    products = [p for p in ctx.html_pages() if p.html and p.html.product_name]
    if not products:
        yield Manual("No product pages detected (og:type=product); check names by hand")
        return
    for page in products:
        assert page.html is not None and page.html.product_name is not None
        name = page.html.product_name.strip()
        if not pattern.fullmatch(name):
            yield Issue(
                page.final_url,
                f'og:title "{snip(name, 150)}" does not match '
                f"/{config.product_name_pattern}/",
            )


# -- META-04 -----------------------------------------------------------------


def _meta04(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.indexable_pages():
        assert page.html is not None
        texts = [t for t in page.html.h1 if t.strip()]
        empty = len(page.html.h1) - len(texts)
        if len(texts) == 1 and empty == 0:
            continue
        detail = f"{len(texts)} non-empty <h1>"
        if empty:
            detail += f", {empty} empty"
        if page.html.h1_logo_only:
            detail += f", {page.html.h1_logo_only} logo-only"
        snippets = "; ".join(page.html.h1_snippets[:2]) or "no <h1>"
        yield Issue(page.final_url, f"{detail}: {snippets}")


# -- META-05 -----------------------------------------------------------------


def _og_image(page: Page) -> str | None:
    if page.html is None:
        return None
    raw = page.html.og.get("og:image") or page.html.og.get("og:image:url")
    return absolutize(page.final_url, raw) if raw else None


def _meta05_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    seen: list[str] = []
    for page in ctx.html_pages():
        image = _og_image(page)
        if image and image not in seen and len(seen) < MAX_OG_IMAGE_PROBES:
            seen.append(image)
            yield ProbeSpec(key=image, url=image, kind="image")


def _meta05(ctx: AuditContext) -> Iterable[Outcome]:
    unprobed: set[str] = set()
    for page in ctx.canonical_pages():
        assert page.html is not None
        og = page.html.og
        missing = [
            k
            for k in ("og:title", "og:description", "og:url", "og:image")
            if not og.get(k)
        ]
        if "twitter:card" not in page.html.twitter:
            missing.append("twitter:card")
        if missing:
            yield Issue(page.final_url, f"Missing {', '.join(missing)}")
        image = _og_image(page)
        if not image:
            continue
        probe = ctx.probe("META-05", image)
        if probe is None:
            unprobed.add(image)
            continue
        if probe.robots_blocked:
            yield Manual(f"og:image {image} is blocked by robots.txt for this crawler")
            continue
        if probe.error or probe.status not in (200, 206):
            yield Issue(
                page.final_url,
                f"og:image not reachable: {image} ({status_evidence(probe)})",
            )
            continue
        declared = og.get("og:image:width", "")
        width = int(declared) if declared.isdigit() else probe.image_width
        if width is None:
            yield Manual(f"Could not read the width of og:image {image}")
        elif width < OG_IMAGE_MIN_WIDTH:
            source = "og:image:width" if declared.isdigit() else "image header"
            yield Issue(
                page.final_url,
                f"og:image is {width}px wide ({source}); "
                f"needs ≥{OG_IMAGE_MIN_WIDTH}px: {image}",
            )
    if unprobed:
        yield Manual(f"{len(unprobed)} og:image(s) not probed (limit)")


# -- META-06 -----------------------------------------------------------------


def _meta06(ctx: AuditContext) -> Iterable[Outcome]:
    pages = ctx.indexable_pages()
    locales: set[str] = set()
    for url in [*ctx.linked_from, *(p.final_url for p in pages)]:
        first = path_of(url).strip("/").split("/", 1)[0]
        if LOCALE_SEGMENT.match(first):
            locales.add(first.lower())
    langs = {p.html.lang.lower() for p in pages if p.html and p.html.lang}
    multi = bool(locales) or len(langs) > 1
    for page in pages:
        assert page.html is not None
        if page.html.lang is None:
            yield Issue(page.final_url, "<html> has no lang attribute")
        if multi and not page.html.hreflang:
            hint = (
                f"locale paths {sorted(locales)[:3]}"
                if locales
                else f"langs {sorted(langs)}"
            )
            yield Issue(
                page.final_url,
                f"Several locales ({hint}) but no hreflang links on this page",
            )


# -- META-07 -----------------------------------------------------------------


def _meta07(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.html_pages():
        assert page.html is not None
        head = page.html.head
        problems: list[str] = []
        for label, inside, outside in (
            ("<title>", head.title_in_head, head.title_outside_head),
            ("canonical", head.canonical_in_head, head.canonical_outside_head),
            ("meta robots", head.robots_in_head, head.robots_outside_head),
        ):
            if outside:
                problems.append(f"{label} outside <head>")
            if inside + outside > 1:
                problems.append(f"{inside + outside}× {label}")
        if not head.has_viewport:
            problems.append("no meta viewport")
        if not head.has_charset and "charset" not in page.content_type.lower():
            problems.append("no charset declaration")
        if not head.has_doctype:
            problems.append("no <!DOCTYPE html>")
        if problems:
            yield Issue(page.final_url, "; ".join(problems))


# -- META-08 -----------------------------------------------------------------


def _robots_values(page: Page) -> list[tuple[str, str]]:
    """(source, raw directive list) for meta robots/googlebot and X-Robots-Tag."""
    values: list[tuple[str, str]] = []
    if page.html is not None:
        for entry in page.html.meta_robots:
            name, _, content = entry.partition(": ")
            values.append((f'<meta name="{name}">', content))
    header = page.headers.get("x-robots-tag")
    if header:
        values.append(("X-Robots-Tag", header))
    return values


def _tokens(content: str) -> list[str]:
    raw = [t.strip() for t in content.split(",")]
    tokens: list[str] = []
    for token in raw:
        if (
            tokens
            and tokens[-1].lower().startswith("unavailable_after")
            and (token[:1].isdigit())
        ):
            tokens[-1] += ", " + token
        elif token:
            tokens.append(token)
    return tokens


def _parse_date(value: str) -> datetime | None:
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _directive_problem(token: str, now: datetime) -> str | None:
    lower = token.lower()
    prefix = lower.split(":", 1)[0].strip()
    if ":" in lower and prefix not in PARAM_DIRECTIVES and " " not in prefix:
        agent, _, rest = lower.partition(":")
        if agent.strip() != "googlebot":
            return None
        lower = rest.strip()
        token = token.split(":", 1)[1].strip()
    name, _, value = lower.partition(":")
    name, value = name.strip(), value.strip()
    if name in ROBOTS_VALID and not value:
        if name == "nosnippet":
            return "nosnippet hides the search snippet"
        return None
    if name in ROBOTS_IGNORED:
        return f"'{name}' is ignored by Google"
    if name in ("max-snippet", "max-video-preview"):
        if not re.fullmatch(r"-?\d+", value):
            return f"invalid value '{token}'"
        if name == "max-snippet" and value == "0":
            return "max-snippet:0 hides the search snippet"
        return None
    if name == "max-image-preview":
        if value not in ("none", "standard", "large"):
            return f"invalid value '{token}'"
        return (
            "max-image-preview:none hides image previews" if value == "none" else None
        )
    if name == "unavailable_after":
        date = _parse_date(token.split(":", 1)[1])
        if date is None:
            return f"unparseable date in '{token}'"
        if date < now:
            return f"expired '{token}'"
        return None
    return f"unknown directive '{token}'"


def _meta08(ctx: AuditContext) -> Iterable[Outcome]:
    try:
        now = datetime.fromisoformat(ctx.crawl.fetched_at)
    except ValueError:
        now = datetime.now(UTC)
    for page in ctx.html_pages():
        for source, content in _robots_values(page):
            problems = [
                p for p in (_directive_problem(t, now) for t in _tokens(content)) if p
            ]
            if problems:
                yield Issue(
                    page.final_url,
                    f"{'; '.join(problems)}: {source} {snip(content, 150)}",
                )


# -- META-09 -----------------------------------------------------------------


def _meta09(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.html_pages():
        assert page.html is not None
        alternates = page.html.hreflang
        if not alternates:
            continue
        self_url = normalize(page.final_url)
        problems: list[str] = []
        hrefs = {normalize(a.href) for a in alternates}
        if self_url not in hrefs:
            problems.append("no self-referencing hreflang")
        if not any(a.lang.lower() == "x-default" for a in alternates):
            problems.append("no x-default")
        bad_codes = [a.lang for a in alternates if not HREFLANG_CODE.match(a.lang)]
        if bad_codes:
            problems.append(f"invalid codes {bad_codes[:3]}")
        for alt in alternates:
            target = normalize(alt.href)
            if target == self_url:
                continue
            res = ctx.resource(target)
            if res is None or res.robots_blocked:
                continue
            if res.status != 200 or res.redirected or res.error:
                problems.append(
                    f"{alt.lang} → {alt.href} is not 200 ({status_evidence(res)})"
                )
                continue
            tpage = ctx.page(target)
            if tpage is not None and tpage.html is not None:
                back = {normalize(a.href) for a in tpage.html.hreflang}
                if self_url not in back:
                    problems.append(f"no return link from {alt.href}")
        if problems:
            yield Issue(page.final_url, "; ".join(problems))


RULES = [
    Rule(
        id="META-01",
        category="meta",
        severity=Severity.high,
        description="Title present, unique, ~30–60 chars; brand from the site config "
        "exactly once, correctly spelled; no repeated suffixes",
        check=_meta01,
        scope="page",
        fix={
            "generic": "Write a unique 30–60 character title per page with the brand "
            "once, e.g. 'Product name | Brand'.",
            "shopify": "Set 'Search engine listing' title per product/collection/page "
            "in Admin; check theme.liquid does not append the shop name twice "
            "(<title>{{ page_title }}{% unless page_title contains shop.name %} | "
            "{{ shop.name }}{% endunless %}</title>).",
            "magento2": "Set Meta Title per product/category/CMS page; check Stores > "
            "Configuration > General > Design > HTML Head > Title Suffix.",
            "woocommerce": "Set the SEO title template in the SEO plugin; avoid "
            "duplicate %%sitename%%.",
        },
    ),
    Rule(
        id="META-02",
        category="meta",
        severity=Severity.medium,
        description="Meta description present, unique, ~70–160 chars; duplicate "
        "clusters reported",
        check=_meta02,
        scope="page",
        fix={
            "generic": "Write a unique 70–160 character meta description per page.",
            "shopify": "Edit 'Search engine listing' > Description for each product, "
            "collection and page in Admin.",
            "magento2": "Set Meta Description per product/category/CMS page "
            "(Search Engine Optimization section).",
            "woocommerce": "Set the meta description in the SEO plugin box per page.",
        },
    ),
    Rule(
        id="META-03",
        category="meta",
        severity=Severity.low,
        description="Product names match the naming pattern from the site config",
        check=_meta03,
        scope="page",
        fix={
            "generic": "Rename the product to follow the agreed naming pattern.",
            "shopify": "Edit the product title in Admin > Products.",
            "magento2": "Edit the product Name in Catalog > Products.",
        },
    ),
    Rule(
        id="META-04",
        category="meta",
        severity=Severity.high,
        description="Exactly one non-empty <h1> per page (not just the logo)",
        check=_meta04,
        scope="page",
        fix={
            "generic": "Use one <h1> with the page's main heading; make logos and "
            "secondary headings non-h1.",
            "shopify": "In header.liquid render the logo in <div>/<a>, not <h1>; make "
            "sure each template's main section outputs one <h1>.",
            "magento2": "Check the theme's logo template (Magento_Theme::html/"
            "header/logo.phtml) and page title block.",
        },
    ),
    Rule(
        id="META-05",
        category="meta",
        severity=Severity.medium,
        description="og:title, og:description, og:url, twitter:card and a reachable "
        "og:image ≥1200px wide",
        check=_meta05,
        probes=_meta05_probes,
        scope="page",
        fix={
            "generic": "Output complete Open Graph/Twitter tags and use a share image "
            "at least 1200px wide.",
            "shopify": "Upload a ≥1200px social sharing image in Online Store > "
            "Preferences; check snippets/meta-tags.liquid outputs og:image:width.",
            "magento2": "Add OG tags via theme layout or an SEO extension; use a "
            "≥1200px base image.",
            "woocommerce": "Set the social image in the SEO plugin (Facebook/Twitter "
            "tab).",
        },
    ),
    Rule(
        id="META-06",
        category="meta",
        severity=Severity.low,
        description="<html lang> set; hreflang present when several locales exist",
        check=_meta06,
        scope="page",
        fix={
            "generic": "Set <html lang> and add hreflang links for every locale "
            "version.",
            "shopify": 'theme.liquid: <html lang="{{ request.locale.iso_code }}">; '
            "Shopify adds hreflang when Markets/languages are published.",
            "magento2": "Set locale per store view; add hreflang via an SEO extension.",
        },
    ),
    Rule(
        id="META-07",
        category="meta",
        severity=Severity.medium,
        description="Head hygiene: title, meta robots, canonical inside <head> and "
        "not duplicated; viewport, charset and doctype present",
        check=_meta07,
        scope="page",
        fix={
            "generic": "Move these tags into <head> once each; add <!DOCTYPE html>, "
            "<meta charset> and <meta name=viewport>.",
            "shopify": "Check theme.liquid and app embeds that inject tags into "
            "<body> or duplicate the canonical.",
            "magento2": "Check custom head blocks in default_head_blocks.xml.",
        },
    ),
    Rule(
        id="META-08",
        category="meta",
        severity=Severity.low,
        description="Robots meta / X-Robots-Tag values valid; no ignored, expired or "
        "snippet-restricting directives",
        check=_meta08,
        scope="page",
        fix={
            "generic": "Use only Google-supported robots values; remove "
            "nosnippet/max-snippet:0 unless intended; update expired "
            "unavailable_after.",
            "shopify": "Check theme.liquid and apps for custom robots meta output.",
            "magento2": "Content > Design > Configuration > Search Engine Robots > "
            "Default Robots.",
        },
    ),
    Rule(
        id="META-09",
        category="meta",
        severity=Severity.medium,
        description="hreflang reciprocity: return links, self reference, x-default, "
        "valid codes, 200 targets",
        check=_meta09,
        scope="page",
        fix={
            "generic": "Every locale version must list all others and itself, plus "
            "x-default, all pointing at 200 canonical URLs.",
            "shopify": "Publish languages/markets in Settings > Markets; remove "
            "hand-written hreflang from theme.liquid that conflicts with Shopify's.",
            "magento2": "Configure an hreflang extension per store view.",
        },
    ),
]
