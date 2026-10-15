"""Rule model, rule contexts and the auto-discovering registry.

A rule module only has to define ``Rule`` instances (or lists of them) at module
level inside ``pss_seo_inspector.rules``; the registry imports every module in the
package and collects them. The crawler never needs to change for a new rule.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal

from ..models import Crawl, Page, Probe, Resource, Severity
from ..robots import RobotsTxt, allow_all, from_status
from ..site_config import SiteConfig
from ..urlutil import host_of, normalize, snip

Scope = Literal["page", "site"]
ProbeKind = Literal["page", "status", "text", "image"]
ALL = "all"


@dataclass(frozen=True)
class ProbeSpec:
    """An extra URL a rule needs fetched during the crawl."""

    key: str
    url: str
    kind: ProbeKind = "page"
    method: Literal["GET", "HEAD"] = "GET"


@dataclass(frozen=True)
class Issue:
    """A rule violation; the engine turns it into a ``Finding``."""

    url: str
    evidence: str
    severity: Severity | None = None


@dataclass(frozen=True)
class Manual:
    """The rule could not decide; a human must check."""

    description: str


Outcome = Issue | Manual


class RobotsView:
    """Parsed robots.txt of the crawled host, for rule checks."""

    def __init__(self, crawl: Crawl) -> None:
        robots = crawl.robots
        if robots is None:
            self.parsed: RobotsTxt = allow_all()
        else:
            self.parsed = from_status(robots.status, robots.text, robots.error)

    def allowed(self, token: str, url: str) -> bool:
        return self.parsed.allowed(token, url)


class _BaseContext:
    crawl: Crawl
    site_config: SiteConfig | None

    @property
    def platform(self) -> str:
        return self.crawl.platform

    @property
    def origin(self) -> str:
        return self.crawl.origin

    def html_pages(self) -> list[Page]:
        """Fetched 200 HTML pages on the crawled host."""
        return [
            p
            for p in self.crawl.pages
            if p.html is not None
            and p.status == 200
            and host_of(p.final_url) == self.crawl.host
        ]

    def absolute(self, url_or_path: str) -> str:
        if url_or_path.startswith(("http://", "https://")):
            return normalize(url_or_path)
        if not url_or_path.startswith("/"):
            url_or_path = "/" + url_or_path
        return normalize(self.crawl.origin + url_or_path)


@dataclass
class ProbeContext(_BaseContext):
    crawl: Crawl
    site_config: SiteConfig | None
    token: str


@dataclass
class AuditContext(_BaseContext):
    crawl: Crawl
    site_config: SiteConfig | None
    _index: dict[str, Resource] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for check in self.crawl.link_checks:
            self._index.setdefault(normalize(check.url), check)
        for probe in self.crawl.probes:
            if probe.kind in ("page", "status"):
                self._index.setdefault(normalize(probe.url), probe)
        for page in self.crawl.pages:
            self._index[normalize(page.url)] = page
        for page in self.crawl.pages:
            if page.html is not None:
                self._index[normalize(page.final_url)] = page

    @cached_property
    def robots(self) -> RobotsView:
        return RobotsView(self.crawl)

    def probes(self, rule_id: str) -> list[Probe]:
        return [p for p in self.crawl.probes if p.rule_id == rule_id]

    def probe(self, rule_id: str, key: str) -> Probe | None:
        for p in self.crawl.probes:
            if p.rule_id == rule_id and p.key == key:
                return p
        return None

    def resource(self, url: str) -> Resource | None:
        """What we know about ``url``: crawled page, link check or probe."""
        return self._index.get(normalize(url))

    def page(self, url: str) -> Page | None:
        found = self._index.get(normalize(url))
        return found if isinstance(found, Page) else None

    def is_internal(self, url: str) -> bool:
        return host_of(url) == self.crawl.host

    @cached_property
    def linked_from(self) -> dict[str, list[str]]:
        """Internal link target → source pages (normalized URLs)."""
        result: dict[str, list[str]] = {}
        for page in self.html_pages():
            assert page.html is not None
            for link in page.html.links:
                if self.is_internal(link.href):
                    sources = result.setdefault(normalize(link.href), [])
                    if page.final_url not in sources:
                        sources.append(page.final_url)
        return result

    def indexable_pages(self) -> list[Page]:
        """200 HTML pages without noindex."""
        return [p for p in self.html_pages() if not is_noindex(p)]

    def canonical_pages(self) -> list[Page]:
        """Indexable pages whose canonical is missing or points to themselves."""
        return [p for p in self.indexable_pages() if canonical_is_self(p)]

    @cached_property
    def sitemap_urls(self) -> list[str]:
        urls: list[str] = []
        for sitemap in self.crawl.sitemaps:
            if sitemap.kind == "urlset":
                urls.extend(normalize(e.loc) for e in sitemap.entries)
        return list(dict.fromkeys(urls))


def robots_directives(resource: Resource, token: str = "googlebot") -> list[str]:
    """Lower-cased directives from meta robots/googlebot and X-Robots-Tag."""
    values: list[str] = []
    html = getattr(resource, "html", None)
    if html is not None:
        for entry in html.meta_robots:
            name, _, content = entry.partition(": ")
            if name in ("robots", token):
                values.extend(v.strip().lower() for v in content.split(","))
    header = resource.headers.get("x-robots-tag", "")
    for part in header.split(","):
        part = part.strip().lower()
        if ":" in part:
            agent, _, rest = part.partition(":")
            if agent.strip() in (
                "unavailable_after",
                "max-snippet",
                "max-image-preview",
                "max-video-preview",
            ):
                values.append(part)
                continue
            if agent.strip() != token:
                continue
            part = rest.strip()
        if part:
            values.append(part)
    return [v for v in values if v]


def unique_pages(pages: Iterable[Page]) -> list[Page]:
    """First page per final_url (a redirect source and its target share one)."""
    seen: dict[str, Page] = {}
    for page in pages:
        seen.setdefault(normalize(page.final_url), page)
    return list(seen.values())


def linked_from_note(ctx: AuditContext, url: str) -> str:
    """'; linked from <first source> +N more' for evidence, or ''."""
    sources = ctx.linked_from.get(normalize(url), [])
    if not sources:
        return ""
    more = f" +{len(sources) - 1} more" if len(sources) > 1 else ""
    return f"; linked from {sources[0]}{more}"


def is_noindex(resource: Resource) -> bool:
    directives = robots_directives(resource)
    return "noindex" in directives or "none" in directives


def noindex_evidence(resource: Resource) -> str:
    html = getattr(resource, "html", None)
    if html is not None:
        for entry, tag in zip(
            html.meta_robots, html.meta_robots_snippets, strict=False
        ):
            if "noindex" in entry.lower() or "none" in entry.lower():
                return str(tag)
    header = resource.headers.get("x-robots-tag")
    return f"X-Robots-Tag: {header}" if header else "no noindex"


def canonical_is_self(page: Page) -> bool:
    if page.html is None or not page.html.canonicals:
        return True
    return normalize(page.html.canonicals[0]) == normalize(page.final_url)


def status_evidence(resource: Resource) -> str:
    """Status plus redirect chain, e.g. ``301 → 302 → 200 (http://… → …)``."""
    if resource.robots_blocked:
        return "blocked by robots.txt (not fetched)"
    if resource.error and not resource.chain:
        return f"error: {resource.error}"
    parts = [f"{hop.status} {hop.url}" for hop in resource.chain]
    text = " → ".join(parts) if parts else f"{resource.status}"
    if resource.error:
        text += f" (error: {resource.error})"
    return snip(text)


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    severity: Severity
    description: str
    check: Callable[[AuditContext], Iterable[Outcome]]
    fix: dict[str, str]
    applies_to: frozenset[str] = frozenset({ALL})
    scope: Scope = "site"
    probes: Callable[[ProbeContext], Iterable[ProbeSpec]] | None = None
    manual: str | None = None

    def __post_init__(self) -> None:
        if "generic" not in self.fix:
            raise ValueError(f"Rule {self.id} needs a 'generic' fix text")

    def applies(self, platform: str) -> bool:
        return ALL in self.applies_to or platform in self.applies_to

    def fix_for(self, platform: str) -> str:
        return self.fix.get(platform, self.fix["generic"])


_REGISTRY: dict[str, Rule] = {}


def _collect(module: object) -> None:
    for value in vars(module).values():
        candidates = value if isinstance(value, list | tuple) else [value]
        for item in candidates:
            if isinstance(item, Rule):
                existing = _REGISTRY.get(item.id)
                if existing is not None and existing is not item:
                    raise ValueError(f"Duplicate rule id {item.id}")
                _REGISTRY[item.id] = item


def all_rules() -> list[Rule]:
    """Every rule from every module in ``pss_seo_inspector.rules``, sorted by id."""
    package = importlib.import_module("pss_seo_inspector.rules")
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        _collect(importlib.import_module(f"{package.__name__}.{info.name}"))
    return sorted(_REGISTRY.values(), key=_sort_key)


def _sort_key(rule: Rule) -> tuple[str, int, str]:
    prefix, _, number = rule.id.partition("-")
    return (prefix, int(number) if number.isdigit() else 0, rule.id)


def get_rule(rule_id: str) -> Rule | None:
    return {r.id: r for r in all_rules()}.get(rule_id)
