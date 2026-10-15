"""MCP server exposing the read-only SEO audit tools over stdio."""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Annotated, Any

import httpx2
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .crawler import DEFAULT_MAX_PAGES, crawl_site, ensure_url
from .engine import UnknownRuleError, run_audit, select_rules
from .fetcher import DEFAULT_RATE, Clock
from .models import Crawl, Severity
from .pagespeed import run_pagespeed
from .platform import GENERIC, PLATFORMS
from .report import summary_lines, write_reports
from .rules.base import ALL, all_rules
from .site_config import SiteConfig, SiteConfigError, load_site_config
from .storage import (
    UnknownCrawlError,
    crawl_dir,
    load_crawl,
    load_findings,
    save_crawl,
)
from .urlutil import domain_of, normalize

MAX_DIFF_LIST = 100

# Test seams: tests replace these with an httpx2.MockTransport and a fake clock.
transport: httpx2.AsyncBaseTransport | None = None
psi_transport: httpx2.AsyncBaseTransport | None = None  # separate seam for PSI calls
clock: Clock | None = None
today: date | None = None


class ReadOnlyMCPServer(MCPServer):
    """MCP server with an explicit closed input surface for read-only tools."""

    _tool_arguments: dict[str, frozenset[str]] = {
        "crawl_site": frozenset({"url", "max_pages", "rate"}),
        "audit_site": frozenset(
            {"url", "crawl_id", "rules", "site_config", "max_pages", "rate"}
        ),
        "audit_url": frozenset({"url", "rules"}),
        "list_rules": frozenset({"platform"}),
        "get_findings": frozenset({"crawl_id", "rule_id", "severity"}),
    }

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Any = None
    ) -> Any:
        allowed = self._tool_arguments.get(name)
        if allowed is not None:
            unknown = sorted(set(arguments) - allowed)
            if unknown:
                raise ToolError(f"Unknown arguments: {', '.join(unknown)}")
        return await super().call_tool(name, arguments, context)


mcp = ReadOnlyMCPServer("pss-seo-inspector")


def _site_config(domain: str, path: str | None) -> SiteConfig | None:
    try:
        return load_site_config(domain, path)
    except SiteConfigError as exc:
        raise ToolError(str(exc)) from None


def _check_crawl_id(start: str) -> None:
    """Reject URLs whose host cannot form a crawl id, before any request."""
    crawl_id = f"{domain_of(start)}/{(today or date.today()).isoformat()}"
    try:
        crawl_dir(crawl_id)
    except UnknownCrawlError:
        raise ToolError(f"Invalid URL host for a crawl: {start}") from None


async def _crawl(
    url: str,
    max_pages: int,
    rate: float,
    config: SiteConfig | None,
    ctx: Context | None = None,
) -> Crawl:
    _check_crawl_id(url)
    try:
        crawl = await crawl_site(
            url,
            max_pages=max_pages,
            rate=rate,
            transport=transport,
            clock=clock,
            site_config=config,
            today=today,
            progress=ctx.report_progress if ctx is not None else None,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    save_crawl(crawl)
    return crawl


def _pagespeed_urls(crawl: Crawl, config: SiteConfig | None) -> list[str]:
    """Select up to 5 URLs to check with PageSpeed Insights."""
    if config is not None and config.pagespeed_urls:
        return list(config.pagespeed_urls[:5])
    html_pages = [
        p.final_url
        for p in crawl.pages
        if p.status == 200 and p.html is not None and p.final_url != crawl.start_url
    ]
    return [crawl.start_url, *html_pages][:5]


def _diff(crawl: Crawl) -> tuple[list[str], list[str]]:
    sitemap: list[str] = []
    for sm in crawl.sitemaps:
        if sm.kind == "urlset":
            sitemap.extend(normalize(e.loc) for e in sm.entries)
    sitemap_set = set(sitemap)
    linked: set[str] = set()
    for page in crawl.pages:
        if page.html is not None:
            linked.update(normalize(link.href) for link in page.html.links)
    linked.add(normalize(crawl.start_url))
    crawled = [
        normalize(p.final_url)
        for p in crawl.pages
        if p.html is not None and normalize(p.url) in linked
    ]
    sitemap_only = [u for u in dict.fromkeys(sitemap) if u not in linked]
    crawl_only = [u for u in dict.fromkeys(crawled) if u not in sitemap_set]
    return sitemap_only, crawl_only


async def _crawl_site(
    ctx: Context,
    url: Annotated[
        str, Field(min_length=1, description="Start URL, e.g. https://example.com")
    ],
    max_pages: Annotated[int, Field(ge=1, le=5000)] = DEFAULT_MAX_PAGES,
    rate: Annotated[float, Field(gt=0, le=10)] = DEFAULT_RATE,
) -> dict[str, Any]:
    """Crawl politely (GET/HEAD only, robots.txt honoured); save crawl.json."""
    start = ensure_url(url)
    _check_crawl_id(start)
    config = _site_config(domain_of(start), None)
    crawl = await _crawl(start, max_pages, rate, config, ctx)
    sitemap_only, crawl_only = _diff(crawl)
    return {
        "crawl_id": crawl.crawl_id,
        "url_count": len(crawl.pages),
        "html_pages": sum(1 for p in crawl.pages if p.html is not None),
        "platform": crawl.platform,
        "capped": crawl.capped,
        "rate_limited": crawl.rate_limited,
        "skipped_robots": len(crawl.skipped_robots),
        "sitemap_only_count": len(sitemap_only),
        "sitemap_only": sitemap_only[:MAX_DIFF_LIST],
        "crawl_only_count": len(crawl_only),
        "crawl_only": crawl_only[:MAX_DIFF_LIST],
        "crawl_path": str(crawl_dir(crawl.crawl_id) / "crawl.json"),
    }


async def _audit_site(
    ctx: Context,
    url: Annotated[str | None, Field(description="Crawl this URL first")] = None,
    crawl_id: Annotated[
        str | None, Field(description="Re-audit a saved crawl: <domain>/<YYYY-MM-DD>")
    ] = None,
    rules: Annotated[list[str] | None, Field(description="Only these rule ids")] = None,
    site_config: Annotated[
        str | None, Field(description="Path to a site config JSON file")
    ] = None,
    max_pages: Annotated[int, Field(ge=1, le=5000)] = DEFAULT_MAX_PAGES,
    rate: Annotated[float, Field(gt=0, le=10)] = DEFAULT_RATE,
) -> dict[str, Any]:
    """Run the rules over a crawl and write report.md, findings.json and urls.csv."""
    if (url is None) == (crawl_id is None):
        raise ToolError("Pass exactly one of 'url' or 'crawl_id'")
    try:
        select_rules(rules)
    except UnknownRuleError as exc:
        raise ToolError(str(exc)) from None
    if crawl_id is not None:
        try:
            crawl = load_crawl(crawl_id)
        except UnknownCrawlError as exc:
            raise ToolError(str(exc)) from None
        config = _site_config(crawl.domain, site_config)
    else:
        assert url is not None
        start = ensure_url(url)
        _check_crawl_id(start)
        config = _site_config(domain_of(start), site_config)
        crawl = await _crawl(start, max_pages, rate, config, ctx)
    # Fetch PageSpeed Insights before auditing so PERF rules see the data in a
    # single pass; this avoids stale "no data" notes and respects the requested
    # rule scope. PSI failures never crash the audit (run_pagespeed is lenient).
    psi_urls = _pagespeed_urls(crawl, config)
    api_key = os.environ.get("PSS_PAGESPEED_API_KEY")
    crawl.pagespeed_results = await run_pagespeed(
        psi_urls, api_key=api_key, transport=psi_transport
    )
    result = run_audit(crawl, config, rules)
    report = write_reports(crawl, result)
    return {
        "crawl_id": crawl.crawl_id,
        "platform": crawl.platform,
        "summary": "\n".join(summary_lines(crawl, result)),
        "report_path": str(report),
        "finding_count": len(result.findings),
        "passed": result.passed,
        "manual": [m.model_dump(mode="json") for m in result.manual],
        "skipped": result.skipped,
        "pagespeed_urls_checked": len(psi_urls),
    }


async def _audit_url(
    url: Annotated[str, Field(min_length=1)],
    rules: Annotated[list[str] | None, Field(description="Only these rule ids")] = None,
) -> dict[str, Any]:
    """Quick single-page check (page-scope rules only); writes no files."""
    try:
        selected = select_rules(rules)
    except UnknownRuleError as exc:
        raise ToolError(str(exc)) from None
    site_rules = [r.id for r in selected if r.scope != "page"] if rules else []
    if site_rules:
        raise ToolError(
            f"audit_url runs page-scope rules only; use audit_site for: "
            f"{', '.join(site_rules)}"
        )
    start = ensure_url(url)
    config = _site_config(domain_of(start), None)
    crawl = await crawl_site(
        start,
        max_pages=1,
        transport=transport,
        clock=clock,
        site_config=config,
        today=today,
        quick=True,
    )
    result = run_audit(crawl, config, rules, scope="page")
    page = crawl.pages[0] if crawl.pages else None
    return {
        "url": start,
        "status": page.status if page else None,
        "platform": crawl.platform,
        "findings": [f.model_dump(mode="json") for f in result.findings],
        "passed": result.passed,
        "manual": [m.model_dump(mode="json") for m in result.manual],
        "skipped": result.skipped,
    }


async def _list_rules(
    platform: Annotated[
        str | None,
        Field(
            description=f"Only rules applying to: {', '.join((GENERIC, *PLATFORMS))}"
        ),
    ] = None,
) -> dict[str, Any]:
    """List rule ids, descriptions, severities and platforms."""
    rules = all_rules()
    if platform is not None:
        rules = [r for r in rules if r.applies(platform)]
    return {
        "rules": [
            {
                "id": r.id,
                "category": r.category,
                "severity": r.severity.value,
                "applies_to": sorted(r.applies_to)
                if ALL not in r.applies_to
                else [ALL],
                "scope": r.scope,
                "description": r.description,
            }
            for r in rules
        ]
    }


async def _get_findings(
    crawl_id: Annotated[str, Field(min_length=1)],
    rule_id: str | None = None,
    severity: Severity | None = None,
) -> dict[str, Any]:
    """Findings of an audited crawl, optionally filtered by rule and severity."""
    try:
        findings = load_findings(crawl_id)
    except UnknownCrawlError as exc:
        raise ToolError(str(exc)) from None
    selected = [
        f
        for f in findings
        if (rule_id is None or f.rule_id == rule_id)
        and (severity is None or f.severity == severity)
    ]
    return {
        "crawl_id": crawl_id,
        "count": len(selected),
        "findings": [f.model_dump(mode="json") for f in selected],
    }


for _function, _name in (
    (_crawl_site, "crawl_site"),
    (_audit_site, "audit_site"),
    (_audit_url, "audit_url"),
    (_list_rules, "list_rules"),
    (_get_findings, "get_findings"),
):
    mcp.add_tool(_function, name=_name, structured_output=True)
    mcp._tool_manager._tools[_name].parameters["additionalProperties"] = False


def main() -> None:
    """Run the MCP server over stdio."""
    asyncio.run(mcp.run_stdio_async())
