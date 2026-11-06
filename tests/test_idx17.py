"""AI search access checks on saved crawls, without network requests."""

from __future__ import annotations

import pytest
from sitekit import ORIGIN, good_crawl, html, page, sitemap

from pss_seo_inspector.engine import run_audit
from pss_seo_inspector.models import Crawl, Finding, Severity

ROBOTS_URL = f"{ORIGIN}/robots.txt"


def _findings(crawl: Crawl) -> list[Finding]:
    return [
        f
        for f in run_audit(crawl, None, ["IDX-17"]).findings
        if f.severity != Severity.info
    ]


def _page(url: str) -> object:
    return page(url, html(url=url))


@pytest.mark.parametrize("bot", ["OAI-SearchBot", "Claude-SearchBot", "PerplexityBot"])
def test_idx17_groups_blocked_pages_and_sitemap_urls_per_line(bot: str) -> None:
    crawl = good_crawl(
        pages=[_page(f"{ORIGIN}/articles/one")],
        sitemaps=[sitemap(f"{ORIGIN}/articles/two")],
        robots_text=f"User-agent: {bot}\nDisallow: /articles/\n",
    )
    findings = _findings(crawl)
    assert len(findings) == 1
    evidence = findings[0].evidence
    assert findings[0].url == ROBOTS_URL
    assert bot in evidence and "Disallow: /articles/ (line 2)" in evidence
    assert "blocks 2 known URL(s)" in evidence
    assert "/articles/one" in evidence and "/articles/two" in evidence
    assert findings[0].severity == Severity.medium


def test_idx17_ignores_urls_a_store_blocks_on_purpose() -> None:
    # Default Shopify-style lines block cart/search/sorted lists for every bot;
    # those URLs are not in the sitemap or among indexable pages.
    crawl = good_crawl(
        skipped_robots=[f"{ORIGIN}/cart", f"{ORIGIN}/search?q=x"],
        robots_text="User-agent: *\nDisallow: /cart\nDisallow: /search\n",
    )
    assert _findings(crawl) == []


def test_idx17_full_block_is_one_finding_per_bot_not_per_url() -> None:
    crawl = good_crawl(
        pages=[_page(f"{ORIGIN}/p{i}") for i in range(100)],
        robots_text="User-agent: *\nDisallow: /\n",
    )
    findings = _findings(crawl)
    assert len(findings) == 3
    assert all("blocks 10" in f.evidence for f in findings)  # 100+ URLs


def test_idx17_deduplicates_original_final_and_sitemap_urls() -> None:
    target = f"{ORIGIN}/articles/one"
    crawl = good_crawl(
        pages=[page(f"{ORIGIN}/old", html(url=target), final_url=target)],
        sitemaps=[sitemap(target + "#section", target)],
        robots_text="User-agent: OAI-SearchBot\nDisallow: /articles/\n",
    )
    findings = _findings(crawl)
    assert len(findings) == 1 and "blocks 1 known URL(s)" in findings[0].evidence


def test_idx17_does_not_apply_robots_to_other_origins() -> None:
    foreign = [
        "https://other.test/articles/one",
        "http://ex.test/articles/two",
        "https://ex.test:8443/articles/three",
    ]
    crawl = good_crawl(
        pages=[_page(url) for url in foreign],
        sitemaps=[sitemap(*foreign)],
        robots_text="User-agent: OAI-SearchBot\nDisallow: /articles/\n",
    )
    assert _findings(crawl) == []


def test_idx17_respects_named_groups_and_longest_allow_rules() -> None:
    crawl = good_crawl(
        pages=[_page(f"{ORIGIN}/articles/public")],
        sitemaps=[],
        robots_text=(
            "User-agent: *\nDisallow: /articles/\n\n"
            "User-agent: OAI-SearchBot\nDisallow: /articles/\n"
            "Allow: /articles/public\n"
        ),
    )
    findings = _findings(crawl)
    assert len(findings) == 2  # Claude-SearchBot and PerplexityBot via '*'
    assert all("the '*' group" in f.evidence for f in findings)
    assert all("OAI-SearchBot" not in f.evidence for f in findings)


def test_idx17_root_block_does_not_claim_guaranteed_absence() -> None:
    findings = _findings(
        good_crawl(robots_text="User-agent: OAI-SearchBot\nDisallow: /\n")
    )
    assert len(findings) == 1
    assert "will not appear" not in findings[0].evidence


@pytest.mark.parametrize("status", [None, 503])
def test_idx17_unavailable_robots_requires_manual_verification(
    status: int | None,
) -> None:
    crawl = good_crawl()
    if status is None:
        crawl.robots = None
    else:
        assert crawl.robots is not None
        crawl.robots.status = status
    result = run_audit(crawl, None, ["IDX-17"])
    assert result.findings == []
    assert any("robots.txt" in item.description for item in result.manual)
