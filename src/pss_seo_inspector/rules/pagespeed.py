"""PERF-01..07: Google PageSpeed Insights performance rules."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Severity
from ..pagespeed import PageSpeedResult  # noqa: F401 — used in type hints
from .base import AuditContext, Issue, Manual, Outcome, Rule


def _perf01(ctx: AuditContext) -> Iterable[Outcome]:
    if not ctx.crawl.pagespeed_results:
        yield Manual("No PageSpeed Insights data available for this crawl")
        return
    for result in ctx.crawl.pagespeed_results:
        if result.error is not None:
            continue
        if result.score is None:
            continue
        if result.score < 50:
            yield Issue(
                result.url,
                f"Performance score {result.score:.0f}/100 (critical)",
                severity=Severity.critical,
            )
        elif result.score < 90:
            yield Issue(
                result.url,
                f"Performance score {result.score:.0f}/100 (needs improvement)",
                severity=Severity.high,
            )


def _perf02(ctx: AuditContext) -> Iterable[Outcome]:
    if not ctx.crawl.pagespeed_results:
        yield Manual("No PageSpeed Insights data available for this crawl")
        return
    for result in ctx.crawl.pagespeed_results:
        if result.lcp_ms is None:
            continue
        if result.lcp_ms > 4000:
            yield Issue(
                result.url,
                f"LCP {result.lcp_ms:.0f} ms (poor, threshold 4000 ms)",
                severity=Severity.critical,
            )
        elif result.lcp_ms > 2500:
            yield Issue(
                result.url,
                f"LCP {result.lcp_ms:.0f} ms (needs improvement, threshold 2500 ms)",
                severity=Severity.high,
            )


def _perf03(ctx: AuditContext) -> Iterable[Outcome]:
    if not ctx.crawl.pagespeed_results:
        yield Manual("No PageSpeed Insights data available for this crawl")
        return
    for result in ctx.crawl.pagespeed_results:
        if result.cls is None:
            continue
        if result.cls > 0.25:
            yield Issue(
                result.url,
                f"CLS {result.cls:.3f} (poor, threshold 0.25)",
                severity=Severity.critical,
            )
        elif result.cls > 0.1:
            yield Issue(
                result.url,
                f"CLS {result.cls:.3f} (needs improvement, threshold 0.10)",
                severity=Severity.medium,
            )


def _perf04(ctx: AuditContext) -> Iterable[Outcome]:
    if not ctx.crawl.pagespeed_results:
        yield Manual("No PageSpeed Insights data available for this crawl")
        return
    for result in ctx.crawl.pagespeed_results:
        if result.inp_ms is None:
            continue
        if result.inp_ms > 500:
            yield Issue(
                result.url,
                f"INP {result.inp_ms:.0f} ms (poor, threshold 500 ms)",
                severity=Severity.critical,
            )
        elif result.inp_ms > 200:
            yield Issue(
                result.url,
                f"INP {result.inp_ms:.0f} ms (needs improvement, threshold 200 ms)",
                severity=Severity.medium,
            )


def _perf05(ctx: AuditContext) -> Iterable[Outcome]:
    if not ctx.crawl.pagespeed_results:
        yield Manual("No PageSpeed Insights data available for this crawl")
        return
    for result in ctx.crawl.pagespeed_results:
        if result.fcp_ms is None:
            continue
        if result.fcp_ms > 3000:
            yield Issue(
                result.url,
                f"FCP {result.fcp_ms:.0f} ms (poor, threshold 3000 ms)",
                severity=Severity.critical,
            )
        elif result.fcp_ms > 1800:
            yield Issue(
                result.url,
                f"FCP {result.fcp_ms:.0f} ms (needs improvement, threshold 1800 ms)",
                severity=Severity.medium,
            )


def _perf06(ctx: AuditContext) -> Iterable[Outcome]:
    if not ctx.crawl.pagespeed_results:
        yield Manual("No PageSpeed Insights data available for this crawl")
        return
    for result in ctx.crawl.pagespeed_results:
        if result.tbt_ms is None:
            continue
        if result.tbt_ms > 600:
            yield Issue(
                result.url,
                f"TBT {result.tbt_ms:.0f} ms (poor, threshold 600 ms)",
                severity=Severity.critical,
            )
        elif result.tbt_ms > 200:
            yield Issue(
                result.url,
                f"TBT {result.tbt_ms:.0f} ms (needs improvement, threshold 200 ms)",
                severity=Severity.medium,
            )


def _perf07(ctx: AuditContext) -> Iterable[Outcome]:
    if not ctx.crawl.pagespeed_results:
        yield Manual("No PageSpeed Insights data available for this crawl")
        return
    for result in ctx.crawl.pagespeed_results:
        if result.ttfb_ms is None:
            continue
        if result.ttfb_ms > 1800:
            yield Issue(
                result.url,
                f"TTFB {result.ttfb_ms:.0f} ms (poor, threshold 1800 ms)",
                severity=Severity.critical,
            )
        elif result.ttfb_ms > 800:
            yield Issue(
                result.url,
                f"TTFB {result.ttfb_ms:.0f} ms (needs improvement, threshold 800 ms)",
                severity=Severity.low,
            )


RULES = [
    Rule(
        id="PERF-01",
        category="performance",
        severity=Severity.high,
        scope="site",
        description="PageSpeed Insights overall performance score ≥ 90",
        check=_perf01,
        fix={
            "generic": (
                "Use PageSpeed Insights or Lighthouse to identify the biggest "
                "performance opportunities (image optimisation, unused JavaScript, "
                "render-blocking resources, caching)."
            )
        },
    ),
    Rule(
        id="PERF-02",
        category="performance",
        severity=Severity.high,
        scope="site",
        description="Largest Contentful Paint (LCP) ≤ 2500 ms",
        check=_perf02,
        fix={
            "generic": (
                "Optimise the LCP element: preload hero images, use a CDN, compress "
                "images to WebP/AVIF, and eliminate render-blocking resources above "
                "the fold."
            )
        },
    ),
    Rule(
        id="PERF-03",
        category="performance",
        severity=Severity.medium,
        scope="site",
        description="Cumulative Layout Shift (CLS) ≤ 0.1",
        check=_perf03,
        fix={
            "generic": (
                "Set explicit width/height on images and video embeds, reserve space "
                "for ads/embeds, and avoid inserting content above existing content "
                "after load."
            )
        },
    ),
    Rule(
        id="PERF-04",
        category="performance",
        severity=Severity.medium,
        scope="site",
        description="Interaction to Next Paint (INP) ≤ 200 ms",
        check=_perf04,
        fix={
            "generic": (
                "Break up long JavaScript tasks, defer non-critical scripts, and use "
                "web workers for heavy computation to keep the main thread free for "
                "user interactions."
            )
        },
    ),
    Rule(
        id="PERF-05",
        category="performance",
        severity=Severity.medium,
        scope="site",
        description="First Contentful Paint (FCP) ≤ 1800 ms",
        check=_perf05,
        fix={
            "generic": (
                "Reduce server response time, eliminate render-blocking resources, "
                "inline critical CSS, and ensure the HTML is served quickly."
            )
        },
    ),
    Rule(
        id="PERF-06",
        category="performance",
        severity=Severity.medium,
        scope="site",
        description="Total Blocking Time (TBT) ≤ 200 ms",
        check=_perf06,
        fix={
            "generic": (
                "Minimise long tasks by code-splitting, deferring third-party scripts, "
                "and removing unused JavaScript and CSS."
            )
        },
    ),
    Rule(
        id="PERF-07",
        category="performance",
        severity=Severity.low,
        scope="site",
        description="Time to First Byte (TTFB) ≤ 800 ms",
        check=_perf07,
        fix={
            "generic": (
                "Improve server response time: use a CDN, enable caching, optimise "
                "database queries, or upgrade hosting infrastructure."
            )
        },
    ),
]
