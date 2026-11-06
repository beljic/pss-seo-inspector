"""Checks for AI search and answer engines (GEO).

Each rule is deterministic and reads only the fetched HTML. Sources: Google Search
Central (data-nosnippet, publication dates, structured-data content policy,
crawlable links, JavaScript SEO basics) and the open-source GEO Optimizer audit
(github.com/Auriti-Labs/geo-optimizer-skill), whose thresholds are kept where noted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import urljoin

from ..models import Severity
from ..urlutil import normalize
from .base import AuditContext, Issue, Manual, Outcome, Rule
from .jsonld import Node, PageData, as_list, page_data

ARTICLE_TYPES = (
    "Article",
    "NewsArticle",
    "BlogPosting",
    "TechArticle",
    "Report",
    "ScholarlyArticle",
    "LiveBlogPosting",
)
SPA_SHELL_MAX_CHARS = 50  # geo-optimizer audit_js.py
SPA_SHELL_MAX_WORDS = 200
NOSNIPPET_WARN = 0.1  # geo-optimizer audit_google_ai.py
NOSNIPPET_FAIL = 0.5
FUTURE_GRACE = timedelta(days=1)
DATE_SPREAD = timedelta(days=90)
MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
)


def _words(text: str) -> int:
    return len(text.split())


MARKS_RE = re.compile(r"[\u00ae\u2122\u00a9\u2120]")  # ® ™ © ℠
PUNCT = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2011": "-", "\u2019": "'",
                       "\u2018": "'", "\u201c": '"', "\u201d": '"'})


def _normal(text: str) -> str:
    """Compare text the way a reader would: no ®/™/©, plain dashes and quotes."""
    flat = MARKS_RE.sub("", unescape(text)).translate(PUNCT)
    return " ".join(flat.split()).casefold()


def _parse_date(value: Any) -> datetime | None:
    """ISO 8601 date or date-time, as an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _crawl_time(ctx: AuditContext) -> datetime:
    return _parse_date(ctx.crawl.fetched_at) or datetime.now(UTC)


# -- IDX-18 -----------------------------------------------------------------


def _idx18(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.indexable_pages():
        html = page.html
        assert html is not None
        if html.a_href_count is None:
            yield Manual(f"{page.final_url}: link counts not collected; re-crawl")
            continue
        if html.a_href_count == 0:
            yield Issue(page.final_url, "No <a href> links on the page")
        elif html.js_link_count * 2 > html.a_href_count:
            yield Issue(
                page.final_url,
                f"{html.js_link_count} of {html.a_href_count} links are javascript: "
                f"or #/ routes: {'; '.join(html.js_link_snippets)}",
            )
        words = _words(html.text)
        if (
            html.spa_root is not None
            and html.spa_root_chars < SPA_SHELL_MAX_CHARS
            and words < SPA_SHELL_MAX_WORDS
        ):
            yield Issue(
                page.final_url,
                f"App shell: {html.spa_root} holds {html.spa_root_chars} characters "
                f"of text and the page {words} words before JavaScript runs",
            )


# -- META-10 ----------------------------------------------------------------


def _meta10(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.indexable_pages():
        html = page.html
        assert html is not None
        total = html.geo_word_count
        if total is None:
            yield Manual(
                f"{page.final_url}: full text word count not collected; re-crawl"
            )
            continue
        for snippet in html.nosnippet_invalid:
            yield Issue(
                page.final_url,
                f"Google ignores data-nosnippet here (only span, div, section): "
                f"{snippet}",
                severity=Severity.low,
            )
        if not total or not html.nosnippet_words:
            continue
        ratio = html.nosnippet_words / total
        if ratio > NOSNIPPET_WARN:
            yield Issue(
                page.final_url,
                f"data-nosnippet hides {html.nosnippet_words} of {total} words "
                f"({ratio:.0%}) from search snippets (and Bing AI summaries)",
                severity=Severity.medium if ratio > NOSNIPPET_FAIL else Severity.low,
            )


# -- SD-09 ------------------------------------------------------------------


def _jsonld_dates(data: PageData) -> list[tuple[str, datetime]]:
    """(field, date) pairs from every JSON-LD node for the future-date check."""
    dates: list[tuple[str, datetime]] = []
    for flat in data.nodes:
        for key in ("datePublished", "dateModified"):
            parsed = _parse_date(flat.node.get(key))
            if parsed is None:
                continue
            dates.append((key, parsed))
    return dates


def _page_identity(data: PageData, reference: Any, page_url: str) -> str | None:
    resolved = (
        data.ids.get(reference, reference)
        if isinstance(reference, str) else data.resolve(reference)
    )
    identity = (
        resolved.get("url") or resolved.get("@id")
        if isinstance(resolved, dict) else resolved
    )
    return normalize(urljoin(page_url, identity)) if isinstance(identity, str) else None


def _primary_article(data: PageData, page_url: str) -> Node | None:
    target = normalize(page_url)
    articles: list[Node] = []
    explicit: list[Node] = []
    for article in data.of_type(*ARTICLE_TYPES, top_only=True):
        identities = {
            identity
            for reference in as_list(article.get("mainEntityOfPage"))
            if (identity := _page_identity(data, reference, page_url)) is not None
        }
        if identities and target not in identities:
            continue
        articles.append(article)
        if target in identities:
            explicit.append(article)
    for webpage in data.of_type("WebPage"):
        identity = _page_identity(data, webpage, page_url)
        if identity is not None and identity != target:
            continue
        for reference in as_list(webpage.get("mainEntity")):
            resolved = data.resolve(reference)
            for article in articles:
                if resolved is article and not any(a is article for a in explicit):
                    explicit.append(article)
    if explicit:
        return explicit[0] if len(explicit) == 1 else None
    return articles[0] if len(articles) == 1 else None


def _date_label_matches(value: str, label: str) -> bool:
    """Recognize common numeric/English labels; other formats remain manual."""
    date = _parse_date(value)
    if date is None:
        return False
    year, month, day = date.year, date.month, date.day
    numeric = (
        rf"\b(?:{year}[-/.]0?{month}[-/.]0?{day}|"
        rf"0?{day}[-/.]0?{month}[-/.]{year}|"
        rf"0?{month}[-/.]0?{day}[-/.]{year})\b"
    )
    name = MONTH_NAMES[month - 1]
    named = (
        rf"\b(?:0?{day}(?:st|nd|rd|th)?\s+(?:{name}|{name[:3]})|"
        rf"(?:{name}|{name[:3]})\.?\s+0?{day}(?:st|nd|rd|th)?)\b"
    )
    return re.search(numeric, label) is not None or re.search(
        named, label, re.IGNORECASE
    ) is not None


def _sd09(ctx: AuditContext) -> Iterable[Outcome]:
    now = _crawl_time(ctx)
    for page in ctx.indexable_pages():
        html = page.html
        assert html is not None
        data = page_data(page)
        jsonld = _jsonld_dates(data)
        meta = {
            key: parsed
            for key, value in html.meta_dates.items()
            if (parsed := _parse_date(value)) is not None
        }
        future = [
            (key, d)
            for key, d in [*jsonld, *meta.items()]
            if d > now + FUTURE_GRACE
        ]
        if future:
            key, d = future[0]
            yield Issue(
                page.final_url,
                f"{key} {d.date().isoformat()} is in the future (crawled "
                f"{now.date().isoformat()})",
            )
            continue
        article = _primary_article(data, page.final_url)
        if article is None:
            if data.of_type(*ARTICLE_TYPES, top_only=True):
                yield Manual(
                    f"{page.final_url}: ambiguous primary Article; "
                    "compare dates by hand"
                )
            continue
        article_dated = any(
            _parse_date(article.get(key)) is not None
            for key in ("datePublished", "dateModified")
        )
        if article_dated and (
            html.time_datetimes is None or html.time_texts is None
        ):
            yield Manual(f"{page.final_url}: visible dates not collected; re-crawl")
        elif article_dated and not any(
            _parse_date(value) is not None for value in html.time_datetimes or []
        ):
            yield Issue(
                page.final_url,
                "Article has a JSON-LD date but no visible dated <time> element",
                severity=Severity.low,
            )
        elif article_dated and not any(
            _date_label_matches(value, label)
            for value, label in zip(
                html.time_datetimes or [], html.time_texts or [], strict=False
            )
        ):
            yield Manual(
                f"{page.final_url}: visible time label does not establish a date; "
                "verify by hand"
            )
        for ld_key, meta_key in (
            ("datePublished", "article:published_time"),
            ("dateModified", "article:modified_time"),
        ):
            ld = _parse_date(article.get(ld_key))
            if ld and meta_key in meta and abs(ld - meta[meta_key]) > DATE_SPREAD:
                yield Issue(
                    page.final_url,
                    f"{ld_key} {ld.date().isoformat()} and {meta_key} "
                    f"{meta[meta_key].date().isoformat()} are more than 90 days apart",
                    severity=Severity.low,
                )


# -- SD-11 ------------------------------------------------------------------


def _sd11(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.indexable_pages():
        html = page.html
        assert html is not None
        visible = _normal(html.geo_text or "")
        data = page_data(page)
        # variants carry their own names ("Oak Frame - Black / Large"); the page shows
        # the group name, so only standalone products and product groups are checked
        variants = {
            id(data.resolve(v))
            for group in data.of_type("ProductGroup")
            for v in as_list(group.get("hasVariant"))
        }
        checks = [
            (n, "name")
            for n in data.of_type("Product", "ProductGroup", top_only=True)
            if id(n) not in variants
        ]
        checks += [(n, "headline") for n in data.of_type(*ARTICLE_TYPES, top_only=True)]
        if checks and (html.geo_text is None or html.geo_text_truncated is None):
            yield Manual(f"{page.final_url}: text completeness not collected; re-crawl")
            continue
        for node, key in checks:
            value = node.get(key)
            if not isinstance(value, str):
                continue
            wanted = _normal(value)
            if len(wanted) >= 3 and wanted not in visible:
                if html.geo_text_truncated:
                    yield Manual(
                        f"{page.final_url}: visible text truncated; verify JSON-LD "
                        "name/headline visibility by hand"
                    )
                    break
                yield Issue(
                    page.final_url,
                    f'JSON-LD {key} "{value.strip()[:120]}" does not appear in the '
                    "visible page text",
                )


# -- CONT-06 ----------------------------------------------------------------


def _cont06(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.html_pages():
        html = page.html
        assert html is not None
        for snippet in html.injection_snippets:
            yield Issue(page.final_url, f"Hidden LLM instruction: {snippet}")


RULES = [
    Rule(
        id="IDX-18",
        category="indexing",
        severity=Severity.high,
        description="Links are <a href> URLs (Google does not recommend javascript: "
        "links; #/ routes are a heuristic) and content is in the HTML, not an empty "
        "app shell",
        check=_idx18,
        scope="page",
        fix={
            "generic": "Render links as <a href=\"/path\"> and server-render (or "
            "pre-render) the main content; AI crawlers and many bots do not run "
            "JavaScript.",
        },
    ),
    Rule(
        id="META-10",
        category="meta",
        severity=Severity.medium,
        description="data-nosnippet only on span/div/section (Google) and hiding at "
        "most 10% of the visible text (threshold is a heuristic)",
        check=_meta10,
        scope="page",
        fix={
            "generic": "Keep data-nosnippet on small blocks (prices, legal text); "
            "remove it from wrappers around the main content.",
        },
    ),
    Rule(
        id="SD-09",
        category="structured",
        severity=Severity.medium,
        description="Publication dates: none in the future and article dates shown on "
        "the page (Google); JSON-LD vs article:*_time within 90 days (heuristic)",
        check=_sd09,
        scope="page",
        fix={
            "generic": "Use the real publish/update dates in JSON-LD and meta, show "
            "them on the page in a <time datetime> element, and keep them in sync.",
        },
    ),
    Rule(
        id="SD-11",
        category="structured",
        severity=Severity.medium,
        description="Product name and Article headline in JSON-LD appear in the "
        "visible page text",
        check=_sd11,
        scope="page",
        fix={
            "generic": "Structured data must describe what the visitor sees: use the "
            "same product name / headline as the page.",
        },
    ),
    Rule(
        id="CONT-06",
        category="content",
        severity=Severity.high,
        description="No hidden instructions for AI systems: LLM control tokens in "
        "comments or hidden text (display:none, opacity 0, font size 0, off-screen)",
        check=_cont06,
        scope="page",
        fix={
            "generic": "Remove the hidden text; check templates, CMS blocks and "
            "third-party scripts for injected content.",
        },
    ),
]
