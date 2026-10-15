"""Pydantic models shared by the crawler, the rule engine and the reports."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .pagespeed import PageSpeedResult

EVIDENCE_SNIPPET_MAX = 300
TEXT_MAX = 100_000


class Severity(StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"

    @property
    def rank(self) -> int:
        """Sort key: critical first."""
        return list(Severity).index(self)


class Hop(BaseModel):
    """One request in a redirect chain."""

    url: str
    status: int
    location: str | None = None


class Link(BaseModel):
    href: str  # absolute, fragment removed
    raw: str  # attribute value as written
    text: str = ""
    rel: list[str] = Field(default_factory=list)
    snippet: str = ""


class Image(BaseModel):
    src: str  # absolute
    raw_src: str = ""
    srcset: str = ""
    alt: str | None = None
    width: str | None = None
    height: str | None = None
    loading: str | None = None
    css_class: str = ""
    snippet: str = ""
    sizes: str = ""
    fetchpriority: str | None = None
    index: int = 0  # DOM order within the page
    link_text: str | None = None  # text of the enclosing <a>; None = not in a link
    landmark: str = ""  # closest header/nav/footer/aside ancestor, "" = content
    picture_modern: bool = False  # in a <picture> with a WebP/AVIF <source>


class Hreflang(BaseModel):
    lang: str
    href: str


class HeadInfo(BaseModel):
    """Placement and presence of head-only elements (for META-07)."""

    has_doctype: bool = False
    has_viewport: bool = False
    has_charset: bool = False
    title_in_head: int = 0
    title_outside_head: int = 0
    canonical_in_head: int = 0
    canonical_outside_head: int = 0
    robots_in_head: int = 0
    robots_outside_head: int = 0


class ParsedHTML(BaseModel):
    """Everything the rules need from one HTML document."""

    titles: list[str] = Field(default_factory=list)
    title_snippet: str = ""
    meta_descriptions: list[str] = Field(default_factory=list)
    meta_description_snippet: str = ""
    meta_robots: list[str] = Field(default_factory=list)  # "name: content"
    meta_robots_snippets: list[str] = Field(default_factory=list)
    canonicals: list[str] = Field(default_factory=list)  # absolute
    canonicals_raw: list[str] = Field(default_factory=list)
    canonical_snippets: list[str] = Field(default_factory=list)
    h1: list[str] = Field(default_factory=list)
    h1_logo_only: int = 0
    h1_snippets: list[str] = Field(default_factory=list)
    lang: str | None = None
    hreflang: list[Hreflang] = Field(default_factory=list)
    og: dict[str, str] = Field(default_factory=dict)
    twitter: dict[str, str] = Field(default_factory=dict)
    links: list[Link] = Field(default_factory=list)
    images: list[Image] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)  # script/css/iframe/media
    jsonld: list[str] = Field(default_factory=list)
    product_name: str | None = None
    body_classes: list[str] = Field(default_factory=list)
    head: HeadInfo = Field(default_factory=HeadInfo)
    # Visible text (no script/style/template/noscript/svg), one line per text node,
    # capped at TEXT_MAX chars. main_text also drops header/nav/footer/aside.
    text: str = ""
    main_text: str = ""
    question_headings: list[str] = Field(default_factory=list)  # h2-h6/summary/dt


class Resource(BaseModel):
    """Result of fetching one URL (redirects followed hop by hop)."""

    url: str
    final_url: str
    status: int | None = None
    chain: list[Hop] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    content_type: str = ""
    error: str | None = None
    robots_blocked: bool = False
    size_bytes: int = 0
    truncated: bool = False

    @property
    def redirected(self) -> bool:
        return len(self.chain) > 1


class Page(Resource):
    depth: int | None = None
    source: Literal["start", "link", "sitemap"] = "link"
    html: ParsedHTML | None = None


class Probe(Resource):
    """An extra request a rule asked for, stored so audits run offline."""

    rule_id: str
    key: str
    kind: Literal["page", "status", "text", "image"]
    text: str | None = None
    html: ParsedHTML | None = None
    image_width: int | None = None
    image_height: int | None = None


class RobotsFile(Resource):
    text: str = ""
    utf8_ok: bool = True


class SitemapEntry(BaseModel):
    loc: str
    lastmod: str | None = None


class SitemapFile(Resource):
    kind: Literal["urlset", "index", "unknown"] = "unknown"
    entries: list[SitemapEntry] = Field(default_factory=list)
    entry_count: int = 0
    uncompressed_bytes: int = 0
    parse_error: str | None = None


class Crawl(BaseModel):
    crawl_id: str
    domain: str
    start_url: str
    origin: str  # scheme://host of the start URL after redirects
    host: str
    fetched_at: str  # ISO 8601 UTC
    user_agent: str
    rate: float
    max_pages: int
    capped: bool = False
    sitemaps_capped: bool = False
    link_checks_capped: bool = False
    rate_limited: bool = False
    probe_errors: list[str] = Field(default_factory=list)
    platform: str = "generic"
    platform_evidence: list[str] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)
    link_checks: list[Resource] = Field(default_factory=list)
    skipped_robots: list[str] = Field(default_factory=list)
    robots: RobotsFile | None = None
    sitemaps: list[SitemapFile] = Field(default_factory=list)
    probes: list[Probe] = Field(default_factory=list)
    pagespeed_results: list[PageSpeedResult] = Field(default_factory=list)


class Finding(BaseModel):
    rule_id: str
    severity: Severity
    url: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    fix: str


class ManualCheck(BaseModel):
    rule_id: str
    description: str


KnownIssueStatus = Literal["still present", "not reproduced", "manual"]


class KnownIssueResult(BaseModel):
    description: str
    status: KnownIssueStatus
    finding_count: int = 0


class AuditResult(BaseModel):
    crawl_id: str
    platform: str
    findings: list[Finding] = Field(default_factory=list)
    passed: list[str] = Field(default_factory=list)
    manual: list[ManualCheck] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    known_issues: list[KnownIssueResult] = Field(default_factory=list)
