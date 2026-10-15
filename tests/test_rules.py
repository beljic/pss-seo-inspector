"""One passing and one failing case per rule, on in-memory crawls."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from sitekit import (
    ORIGIN,
    good_crawl,
    good_pages,
    good_probes,
    html,
    page,
    probe,
    resource,
    sitemap,
)

from pss_seo_inspector.engine import run_audit
from pss_seo_inspector.models import Crawl, Hop, Severity
from pss_seo_inspector.pagespeed import PageSpeedResult
from pss_seo_inspector.rules.base import all_rules
from pss_seo_inspector.rules.platform import SHOPIFY_DEFAULT_DISALLOWS
from pss_seo_inspector.site_config import SiteConfig

A = f"{ORIGIN}/a"
HOME = f"{ORIGIN}/"
SHOP_ROBOTS = "User-agent: *\n" + "".join(
    f"Disallow: {d}\n" for d in SHOPIFY_DEFAULT_DISALLOWS
)

Case = Callable[[], tuple[Crawl, SiteConfig | None]]


def plain(crawl: Crawl) -> tuple[Crawl, SiteConfig | None]:
    return crawl, None


def with_home_body(body: str, **kw: object) -> list:  # type: ignore[type-arg]
    pages = good_pages()
    pages[0] = page(
        HOME,
        html(url=HOME, title="Home page of the example test shop", body=body, **kw),  # type: ignore[arg-type]
    )
    return pages


def replace_page(url: str, text: str, **kw: object) -> list:  # type: ignore[type-arg]
    pages = [p for p in good_pages() if p.url != url]
    pages.append(page(url, text, **kw))  # type: ignore[arg-type]
    return pages


def add_probe(*extra: object) -> list:  # type: ignore[type-arg]
    return [*good_probes(), *extra]


PASS: dict[str, Case] = {
    rule_id: (lambda: plain(good_crawl()))
    for rule_id in [r.id for r in all_rules() if "all" in r.applies_to]
}
PASS["IDX-10"] = lambda: (good_crawl(), SiteConfig(legacy_urls=[]))
PASS["META-03"] = lambda: (
    good_crawl(
        pages=replace_page(
            A,
            html(
                url=A,
                head_extra='<meta property="og:type" content="product">',
                title="AIM Filter in Frame 72",
            ),
        )
    ),
    SiteConfig(product_name_pattern=r"\w+ Filter in Frame \d+"),
)
PASS["SHOP-01"] = lambda: plain(
    good_crawl(
        platform="shopify",
        probes=add_probe(
            probe(
                "SHOP-01", "vendors", f"{ORIGIN}/collections/vendors?q=x", status=404
            ),
            probe("SHOP-01", "types", f"{ORIGIN}/collections/types?q=x", status=404),
        ),
    )
)
PASS["SHOP-02"] = lambda: plain(
    good_crawl(
        platform="shopify",
        probes=add_probe(
            probe(
                "SHOP-02",
                "collections-all",
                f"{ORIGIN}/collections/all",
                text='<html><head><meta name="robots" content="noindex"></head></html>',
            )
        ),
    )
)
PASS["SHOP-03"] = lambda: plain(good_crawl(platform="shopify", robots_text=SHOP_ROBOTS))
PASS["MAG-01"] = lambda: plain(
    good_crawl(
        platform="magento2",
        pages=replace_page(
            A,
            html(url=f"{ORIGIN}/p.html", body="").replace(
                "<body>", '<body class="catalog-product-view">'
            ),
        ),
    )
)
PASS["MAG-02"] = lambda: plain(good_crawl(platform="magento2"))
PASS["WP-01"] = lambda: plain(good_crawl(platform="woocommerce"))

_GOOD_PSI = PageSpeedResult(
    url=f"{ORIGIN}/",
    strategy="mobile",
    score=95.0,
    lcp_ms=1000.0,
    cls=0.05,
    inp_ms=100.0,
    fcp_ms=900.0,
    tbt_ms=50.0,
    ttfb_ms=200.0,
)
for _perf_id in (
    "PERF-01",
    "PERF-02",
    "PERF-03",
    "PERF-04",
    "PERF-05",
    "PERF-06",
    "PERF-07",
):
    PASS[_perf_id] = lambda: plain(good_crawl(pagespeed_results=[_GOOD_PSI]))

PASS["CONT-01"] = lambda: (
    good_crawl(),
    SiteConfig.model_validate(
        {
            "facts": [
                {
                    "name": "return window",
                    "pattern": r"(\w+)\s+days\s+from\s+(delivery|purchase)",
                    "expected": "fifteen delivery",
                }
            ]
        }
    ),
)


def ld(data: object) -> str:
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


FULL_OFFER = {
    "@type": "Offer",
    "price": "49.00",
    "priceCurrency": "USD",
    "availability": "https://schema.org/InStock",
    "url": A,
}


def product(**overrides: object) -> dict[str, object]:
    node: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "AIM Filter in Frame 72",
        "image": f"{ORIGIN}/aim.jpg",
        "description": "Filter",
        "sku": "AIM-72",
        "mpn": "AIM72",
        "brand": {"@type": "Brand", "name": "Example"},
        "aggregateRating": {"@type": "AggregateRating", "ratingValue": 5},
        "offers": dict(FULL_OFFER),
    }
    node.update(overrides)
    return node


def product_page(*nodes: object, body: str = "") -> list:  # type: ignore[type-arg]
    blocks = "".join(ld(n) for n in nodes)
    return replace_page(A, html(url=A, head_extra=blocks, body=body), depth=1)


def facts_config(expected: str | None = None) -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "facts": [
                {
                    "name": "return window",
                    "pattern": r"(\d+)\s*days?\s+from\s+(delivery|purchase)",
                    "expected": expected,
                }
            ]
        }
    )


FAIL: dict[str, Case] = {
    "IDX-01": lambda: plain(
        good_crawl(
            probes=add_probe(
                probe(
                    "IDX-01",
                    "cart",
                    f"{ORIGIN}/cart",
                    text="<html><title>Cart</title></html>",
                )
            )
        )
    ),
    "IDX-02": lambda: plain(
        good_crawl(
            probes=[
                probe(
                    "IDX-02",
                    "random",
                    f"{ORIGIN}/pss-404-check-x",
                    text="<html>Home</html>",
                )
            ]
        )
    ),
    "IDX-03": lambda: plain(
        good_crawl(pages=replace_page(A, html(url=A, canonical=None), depth=1))
    ),
    "IDX-04": lambda: plain(
        good_crawl(
            pages=[
                *with_home_body(f'<a href="{A}">About</a><a href="{ORIGIN}/b">B</a>'),
                page(
                    f"{ORIGIN}/b",
                    html(
                        url=f"{ORIGIN}/b",
                        canonical=A,
                        title="Duplicate b page of the example shop",
                    ),
                    depth=1,
                ),
            ]
        )
    ),
    "IDX-05": lambda: plain(
        good_crawl(
            pages=with_home_body(f'<a href="{A}">About</a><a href="/gone">Gone</a>'),
            link_checks=[resource(f"{ORIGIN}/gone", 404)],
        )
    ),
    "IDX-06": lambda: plain(
        good_crawl(
            pages=with_home_body(
                f'<a href="{A}">About</a><a href="/About?utm_source=x">About</a>'
            )
        )
    ),
    "IDX-07": lambda: plain(
        good_crawl(sitemaps=[sitemap(HOME, A, f"{ORIGIN}/orphan")])
    ),
    "IDX-08": lambda: plain(
        good_crawl(
            sitemaps=[sitemap(HOME, A, f"{ORIGIN}/gone")],
            link_checks=[resource(f"{ORIGIN}/gone", 404)],
        )
    ),
    "IDX-09": lambda: plain(
        good_crawl(
            probes=[
                *good_probes()[:3],
                probe("IDX-09", "http://ex.test/", "http://ex.test/", kind="status"),
            ]
        )
    ),
    "IDX-10": lambda: (
        good_crawl(
            probes=add_probe(
                probe(
                    "IDX-10",
                    "https://old.test/x",
                    "https://old.test/x",
                    status=404,
                    kind="status",
                )
            )
        ),
        SiteConfig(legacy_urls=["https://old.test/x"]),
    ),
    "IDX-11": lambda: plain(good_crawl(robots_text="User-agent: *\nCrawl-delay: 10\n")),
    "IDX-12": lambda: plain(
        good_crawl(
            pages=replace_page(
                A,
                html(url=A, head_extra='<meta name="robots" content="noindex">'),
                depth=1,
            ),
            robots_text="User-agent: Googlebot\nDisallow: /a\n",
        )
    ),
    "IDX-13": lambda: plain(good_crawl(sitemaps=[sitemap(HOME, A, lastmod=None)])),
    "IDX-14": lambda: plain(
        good_crawl(
            pages=replace_page(A, html(url=A, canonical=f"{ORIGIN}/gone"), depth=1),
            link_checks=[resource(f"{ORIGIN}/gone", 404)],
        )
    ),
    "IDX-15": lambda: plain(
        good_crawl(
            pages=with_home_body(
                f'<a href="{A}">About</a><img src="http://cdn.test/x.png" alt="x">'
            )
        )
    ),
    "IDX-16": lambda: plain(
        good_crawl(pages=with_home_body(f'<a href="{A}" rel="nofollow">About</a>'))
    ),
    "IDX-17": lambda: plain(
        good_crawl(robots_text="User-agent: OAI-SearchBot\nDisallow: /\n")
    ),
    "META-01": lambda: (
        good_crawl(),
        SiteConfig(brand="Acme Optics"),
    ),
    "META-02": lambda: plain(
        good_crawl(pages=replace_page(A, html(url=A, desc=""), depth=1))
    ),
    "META-03": lambda: (
        good_crawl(
            pages=replace_page(
                A,
                html(
                    url=A,
                    head_extra='<meta property="og:type" content="product">',
                    title="Aim filter frame",
                ),
            )
        ),
        SiteConfig(product_name_pattern=r"\w+ Filter in Frame \d+"),
    ),
    "META-04": lambda: plain(
        good_crawl(pages=replace_page(A, html(url=A, body="<h1>Second</h1>")))
    ),
    "META-05": lambda: plain(
        good_crawl(pages=replace_page(A, html(url=A, og_width="600")))
    ),
    "META-06": lambda: plain(good_crawl(pages=replace_page(A, html(url=A, lang=None)))),
    "META-07": lambda: plain(
        good_crawl(
            pages=replace_page(
                A,
                html(url=A, canonical=None, body=f'<link rel="canonical" href="{A}">'),
            )
        )
    ),
    "META-08": lambda: plain(
        good_crawl(
            pages=replace_page(
                A, html(url=A, head_extra='<meta name="robots" content="noodp">')
            )
        )
    ),
    "META-09": lambda: plain(
        good_crawl(
            pages=replace_page(
                A,
                html(
                    url=A, head_extra=f'<link rel="alternate" hreflang="en" href="{A}">'
                ),
            )
        )
    ),
    "SHOP-01": lambda: plain(
        good_crawl(
            platform="shopify",
            probes=add_probe(
                probe(
                    "SHOP-01",
                    "vendors",
                    f"{ORIGIN}/collections/vendors?q=x",
                    text="<html><title>Vendors</title></html>",
                ),
            ),
        )
    ),
    "SHOP-02": lambda: plain(
        good_crawl(
            platform="shopify",
            pages=with_home_body(
                f'<a href="{A}">About</a><a href="/collections/all">All</a>'
            ),
            probes=add_probe(
                probe(
                    "SHOP-02",
                    "collections-all",
                    f"{ORIGIN}/collections/all",
                    text="<html><title>All</title></html>",
                )
            ),
        )
    ),
    "SHOP-03": lambda: plain(good_crawl(platform="shopify")),
    "MAG-01": lambda: plain(
        good_crawl(
            platform="magento2",
            pages=replace_page(
                A,
                html(url=A, canonical=f"{ORIGIN}/men/shoes/p.html").replace(
                    "<body>", '<body class="catalog-product-view">'
                ),
            ),
        )
    ),
    "MAG-02": lambda: plain(
        good_crawl(
            platform="magento2",
            pages=with_home_body(f'<a href="{A}?___store=default">About</a>'),
        )
    ),
    "WP-01": lambda: plain(
        good_crawl(
            platform="woocommerce",
            pages=replace_page(
                A,
                html(url=A).replace(
                    "<body>", '<body class="attachment single-attachment">'
                ),
            ),
        )
    ),
    "SD-00": lambda: plain(
        good_crawl(pages=product_page(body='<script type="application/ld+json">{bad'))
    ),
    "SD-01": lambda: plain(
        good_crawl(
            pages=product_page(
                product(offers={k: v for k, v in FULL_OFFER.items() if k != "url"})
            )
        )
    ),
    "SD-02": lambda: plain(good_crawl(pages=with_home_body(f'<a href="{A}">A</a>'))),
    "SD-03": lambda: plain(good_crawl(pages=product_page(product()))),
    "SD-04": lambda: plain(
        good_crawl(
            pages=with_home_body(
                "<h2>How long is shipping?</h2><h2>Can I return it?</h2>"
                "<h3>Do you ship abroad?</h3>"
            )
        )
    ),
    "SD-05": lambda: plain(
        good_crawl(
            pages=with_home_body(
                ld(
                    {
                        "@type": "WebSite",
                        "url": HOME,
                        "potentialAction": {
                            "@type": "SearchAction",
                            "target": f"{ORIGIN}/search?q={{query}}",
                            "query-input": "required name=search_term_string",
                        },
                    }
                )
            )
        )
    ),
    "SD-06": lambda: plain(
        good_crawl(
            pages=with_home_body(ld({"@type": "ClaimReview", "claimReviewed": "x"}))
        )
    ),
    "SD-07": lambda: plain(good_crawl(pages=product_page(product()))),
    "SD-08": lambda: plain(good_crawl(pages=with_home_body(f'<a href="{A}">A</a>'))),
    "IMG-01": lambda: plain(
        good_crawl(pages=with_home_body('<img src="/x.webp" width="10" height="10">'))
    ),
    "IMG-02": lambda: plain(
        good_crawl(
            pages=with_home_body(
                '<img src="/big.webp" alt="Big" width="100" height="100">'
            ),
            probes=add_probe(
                probe(
                    "IMG-02",
                    f"{ORIGIN}/big.webp",
                    f"{ORIGIN}/big.webp",
                    kind="image",
                    image_width=1000,
                    image_height=1000,
                )
            ),
        )
    ),
    "IMG-03": lambda: plain(
        good_crawl(pages=with_home_body('<img src="/x.webp" alt="X">'))
    ),
    "CONT-01": lambda: (
        good_crawl(
            pages=[
                *with_home_body("<p>Returns accepted 15 days from delivery.</p>"),
                page(
                    f"{ORIGIN}/faq",
                    html(
                        url=f"{ORIGIN}/faq",
                        body="<p>You can return within 15 days from purchase.</p>",
                    ),
                ),
            ]
        ),
        facts_config(),
    ),
    "CONT-02": lambda: plain(
        good_crawl(pages=with_home_body("<p>Our filters cut the the glare.</p>"))
    ),
    "CONT-03": lambda: plain(good_crawl(pages=with_home_body("<p>Short.</p>"))),
    "PERF-01": lambda: plain(
        good_crawl(
            pagespeed_results=[
                PageSpeedResult(
                    url=f"{ORIGIN}/",
                    strategy="mobile",
                    score=60.0,  # needs improvement: 50–89
                )
            ]
        )
    ),
    "PERF-02": lambda: plain(
        good_crawl(
            pagespeed_results=[
                PageSpeedResult(
                    url=f"{ORIGIN}/",
                    strategy="mobile",
                    lcp_ms=3000.0,  # needs improvement: 2500–4000
                )
            ]
        )
    ),
    "PERF-03": lambda: plain(
        good_crawl(
            pagespeed_results=[
                PageSpeedResult(
                    url=f"{ORIGIN}/",
                    strategy="mobile",
                    cls=0.15,  # needs improvement: 0.1–0.25
                )
            ]
        )
    ),
    "PERF-04": lambda: plain(
        good_crawl(
            pagespeed_results=[
                PageSpeedResult(
                    url=f"{ORIGIN}/",
                    strategy="mobile",
                    inp_ms=300.0,  # needs improvement: 200–500
                )
            ]
        )
    ),
    "PERF-05": lambda: plain(
        good_crawl(
            pagespeed_results=[
                PageSpeedResult(
                    url=f"{ORIGIN}/",
                    strategy="mobile",
                    fcp_ms=2000.0,  # needs improvement: 1800–3000
                )
            ]
        )
    ),
    "PERF-06": lambda: plain(
        good_crawl(
            pagespeed_results=[
                PageSpeedResult(
                    url=f"{ORIGIN}/",
                    strategy="mobile",
                    tbt_ms=300.0,  # needs improvement: 200–600
                )
            ]
        )
    ),
    "PERF-07": lambda: plain(
        good_crawl(
            pagespeed_results=[
                PageSpeedResult(
                    url=f"{ORIGIN}/",
                    strategy="mobile",
                    ttfb_ms=1000.0,  # needs improvement: 800–1800
                )
            ]
        )
    ),
}


def test_every_rule_has_a_pass_and_a_fail_case() -> None:
    ids = {r.id for r in all_rules()}
    assert ids == set(FAIL)
    assert ids == set(PASS)


@pytest.mark.parametrize("rule_id", sorted(PASS))
def test_rule_passes(rule_id: str) -> None:
    crawl, config = PASS[rule_id]()
    result = run_audit(crawl, config, [rule_id])
    assert [f for f in result.findings if f.rule_id == rule_id] == []
    assert rule_id in result.passed, result.manual


@pytest.mark.parametrize("rule_id", sorted(FAIL))
def test_rule_fails(rule_id: str) -> None:
    crawl, config = FAIL[rule_id]()
    result = run_audit(crawl, config, [rule_id])
    findings = [f for f in result.findings if f.rule_id == rule_id]
    assert findings, (result.manual, result.skipped)
    for f in findings:
        assert f.url and f.evidence and f.fix
        assert len(f.evidence) <= 300


def test_platform_rule_is_skipped_on_other_platform() -> None:
    result = run_audit(good_crawl(), None, ["SHOP-01", "MAG-01", "WP-01"])
    assert result.skipped == ["MAG-01", "SHOP-01", "WP-01"]
    assert result.findings == [] and result.passed == []


def test_idx17_training_bots_and_missing_llms_are_info() -> None:
    probes = [p for p in good_probes() if p.rule_id != "IDX-17"]
    probes.append(
        probe("IDX-17", "llms.txt", f"{ORIGIN}/llms.txt", status=404, kind="text")
    )
    crawl = good_crawl(probes=probes, robots_text="User-agent: GPTBot\nDisallow: /\n")
    findings = run_audit(crawl, None, ["IDX-17"]).findings
    assert {f.severity for f in findings} == {Severity.info}
    assert len(findings) == 2


def test_idx03_shopify_collection_product_must_canonicalize_to_product() -> None:
    url = f"{ORIGIN}/collections/frames/products/aim"
    good = page(url, html(url=url, canonical=f"{ORIGIN}/products/aim"))
    bad = page(url, html(url=url))
    for p, expected in ((good, 0), (bad, 1)):
        crawl = good_crawl(platform="shopify", pages=[*good_pages(), p])
        findings = run_audit(crawl, None, ["IDX-03"]).findings
        assert len(findings) == expected


def test_idx05_reports_redirect_chain() -> None:
    chain = [
        Hop(url=f"{ORIGIN}/x", status=301, location=f"{ORIGIN}/y"),
        Hop(url=f"{ORIGIN}/y", status=302, location=A),
        Hop(url=A, status=200),
    ]
    crawl = good_crawl(
        pages=with_home_body(f'<a href="{A}">About</a><a href="/x">X</a>'),
        link_checks=[resource(f"{ORIGIN}/x", 200, chain)],
    )
    [finding] = run_audit(crawl, None, ["IDX-05"]).findings
    assert "2 hops" in finding.evidence and "301" in finding.evidence


def test_meta01_brand_rules() -> None:
    config = SiteConfig(brand="Acme Optics", brand_wrong_spellings=["Acme optics"])
    pages = [
        page(
            HOME,
            html(url=HOME, title="Filters in frames | Acme Optics | Acme Optics"),
        ),
        page(A, html(url=A, title="About the frames and filters | Acme optics")),
    ]
    findings = run_audit(good_crawl(pages=pages), config, ["META-01"]).findings
    evidence = " ".join(f.evidence for f in findings)
    assert "Repeated title segment" in evidence
    assert "Wrong brand spelling 'Acme optics'" in evidence


def test_known_issues_reverified() -> None:
    config = SiteConfig.model_validate(
        {
            "known_issues": [
                {"description": "dup descriptions", "rule_ids": ["META-02"]},
                {"description": "missing h1", "rule_ids": ["META-04"]},
                {"description": "policy text conflict"},
            ]
        }
    )
    pages = good_pages()
    pages[1] = page(A, html(url=A, title="Another page title of the example shop"))
    result = run_audit(good_crawl(pages=pages), config, ["META-02", "META-04"])
    assert [k.status for k in result.known_issues] == [
        "still present",
        "not reproduced",
        "manual",
    ]


# -- story 2: structured data, images, content (I/O matrix) ------------------


def findings_for(crawl: Crawl, config: SiteConfig | None, rule_id: str) -> list:  # type: ignore[type-arg]
    return [f for f in run_audit(crawl, config, [rule_id]).findings]


def test_sd00_invalid_block_reported_and_skipped_by_other_rules() -> None:
    pages = product_page(product(), body='<script type="application/ld+json">{bad')
    result = run_audit(good_crawl(pages=pages), None, ["SD-00", "SD-01"])
    [sd00] = [f for f in result.findings if f.rule_id == "SD-00"]
    assert sd00.url == A and "{bad" in sd00.evidence
    assert "SD-01" in result.passed  # the valid Product block is still checked


def test_sd01_required_and_recommended_fields() -> None:
    bare = {"@type": "Product", "name": "AIM", "offers": {"@type": "Offer"}}
    findings = findings_for(good_crawl(pages=product_page(bare)), None, "SD-01")
    by_severity = {f.severity: f.evidence for f in findings}
    assert "offers.availability" in by_severity[Severity.high]
    assert "image" in by_severity[Severity.high]
    assert '"@type":"Product"' in by_severity[Severity.high]
    assert "sku" in by_severity[Severity.low]
    assert "aggregateRating/review" in by_severity[Severity.low]


def test_sd01_og_product_without_jsonld() -> None:
    pages = replace_page(
        A, html(url=A, head_extra='<meta property="og:type" content="product">')
    )
    [finding] = findings_for(good_crawl(pages=pages), None, "SD-01")
    assert "without Product JSON-LD" in finding.evidence


def test_sd01_duplicate_products_with_conflicting_price() -> None:
    app = product(offers={**FULL_OFFER, "price": "59.00"})
    pages = product_page(product(), app)
    findings = findings_for(good_crawl(pages=pages), None, "SD-01")
    [dup] = [f for f in findings if f.severity == Severity.medium]
    assert "2 Product nodes" in dup.evidence
    assert "49.00 vs 59.00" in dup.evidence


def test_sd01_ignores_products_nested_in_item_list() -> None:
    item_list = {
        "@type": "ItemList",
        "itemListElement": [{"@type": "Product", "name": "x"}],
    }
    assert findings_for(good_crawl(pages=product_page(item_list)), None, "SD-01") == []


def test_no_product_pages_pass_sd01_sd03_sd07() -> None:
    result = run_audit(good_crawl(), None, ["SD-01", "SD-03", "SD-07"])
    assert result.passed == ["SD-01", "SD-03", "SD-07"]


def test_sd03_shopify_collection_needs_breadcrumbs() -> None:
    url = f"{ORIGIN}/collections/frames"
    pages = [*good_pages(), page(url, html(url=url))]
    crawl = good_crawl(platform="shopify", pages=pages)
    [finding] = findings_for(crawl, None, "SD-03")
    assert finding.url == url and "Category" in finding.evidence
    crumbs = ld({"@type": "BreadcrumbList", "itemListElement": []})
    pages[-1] = page(url, html(url=url, head_extra=crumbs))
    assert (
        findings_for(good_crawl(platform="shopify", pages=pages), None, "SD-03") == []
    )


def test_sd04_faq_answer_must_be_visible() -> None:
    faq = {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": "How long is shipping?",
                "acceptedAnswer": {"@type": "Answer", "text": "<p>Two days.</p>"},
            },
            {
                "@type": "Question",
                "name": "Hidden question?",
                "acceptedAnswer": {"@type": "Answer", "text": "Secret answer."},
            },
        ],
    }
    body = "<h2>How long is shipping?</h2><p>Two   days.</p>"
    [finding] = findings_for(
        good_crawl(pages=product_page(faq, body=body)), None, "SD-04"
    )
    assert finding.severity == Severity.info
    assert "Hidden question?" in finding.evidence


def test_sd05_valid_target_still_notes_retirement() -> None:
    site = {
        "@type": "WebSite",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": "/s?q={search_term_string}",
            },
            "query-input": "required name=search_term_string",
        },
    }
    [finding] = findings_for(good_crawl(pages=with_home_body(ld(site))), None, "SD-05")
    assert "retired" in finding.evidence and finding.severity == Severity.info


def test_sd07_organization_policies_cover_offers() -> None:
    org = {
        "@type": "OnlineStore",
        "name": "Ex",
        "hasMerchantReturnPolicy": {
            "@type": "MerchantReturnPolicy",
            "returnPolicyCountry": "US",
        },
        "hasShippingService": {"@type": "ShippingService"},
    }
    pages = product_page(product(), org)
    assert findings_for(good_crawl(pages=pages), None, "SD-07") == []
    org["hasMerchantReturnPolicy"] = {"@type": "MerchantReturnPolicy"}
    [finding] = findings_for(
        good_crawl(pages=product_page(product(), org)), None, "SD-07"
    )
    assert "returnPolicyCountry" in finding.evidence


def test_sd08_duplicate_website_and_missing_alternate_name() -> None:
    two = ld({"@type": "WebSite", "name": "A", "url": HOME}) + ld(
        {"@type": "WebSite", "name": "B", "url": HOME}
    )
    findings = findings_for(good_crawl(pages=with_home_body(two)), None, "SD-08")
    assert any("2 WebSite nodes" in f.evidence for f in findings)
    assert any(f.severity == Severity.info for f in findings)


def test_img01_empty_alt_rules() -> None:
    body = (
        '<img src="/deco.webp" alt="">'  # decorative: fine
        '<a href="/a"><img src="/link.webp" alt=""></a>'  # link without text
        '<img src="/logo.webp" class="site-logo" alt="">'
    )
    findings = findings_for(good_crawl(pages=with_home_body(body)), None, "IMG-01")
    evidence = " ".join(f.evidence for f in findings)
    assert len(findings) == 2
    assert "link.webp" in evidence and "logo.webp" in evidence
    assert "deco.webp" not in evidence


def test_img02_srcset_without_sizes_and_sizes_px() -> None:
    body = (
        '<img src="/a.webp" alt="a" srcset="/a.webp 400w, /b.webp 800w">'
        '<img src="/c.webp" alt="c" srcset="/c1.webp 900w, /c2.webp 1800w" '
        'sizes="(min-width: 990px) 330px, 200px">'
    )
    findings = findings_for(good_crawl(pages=with_home_body(body)), None, "IMG-02")
    evidence = [f.evidence for f in findings]
    assert any("no sizes" in e for e in evidence)
    assert any("900w for a 330px slot" in e for e in evidence)


def test_img02_probe_limit_manual() -> None:
    from pss_seo_inspector.rules.base import ProbeContext
    from pss_seo_inspector.rules.images import MAX_IMAGE_PROBES, _image_probes

    body = "".join(f'<img src="/i{n}.webp" alt="i">' for n in range(105))
    crawl = good_crawl(pages=with_home_body(body))
    specs = list(_image_probes(ProbeContext(crawl=crawl, site_config=None, token="t")))
    assert len(specs) == MAX_IMAGE_PROBES
    crawl.probes.extend(
        probe("IMG-02", s.key, s.url, kind="image", image_width=10) for s in specs
    )
    result = run_audit(crawl, None, ["IMG-02"])
    assert [m.description for m in result.manual] == [
        "5 image(s) not probed (limit 100); check their intrinsic size by hand"
    ]


def test_img03_format_and_lazy_lcp() -> None:
    body = (
        '<header><img src="/logo.png" alt="L" width="1" height="1" loading="lazy">'
        "</header>"
        '<img src="/hero.jpg" alt="H" width="1" height="1" loading="lazy">'
        '<img src="https://cdn.shopify.com/s/files/x.jpg" alt="S" width="1" '
        'height="1">'
    )
    findings = findings_for(good_crawl(pages=with_home_body(body)), None, "IMG-03")
    lazy = [f for f in findings if f.severity == Severity.medium]
    assert len(lazy) == 1 and "hero.jpg" in lazy[0].evidence
    formats = [f.evidence for f in findings if f.evidence.startswith("Served as")]
    assert len(formats) == 2  # logo.png and hero.jpg; Shopify CDN negotiates
    assert not any("cdn.shopify.com" in e for e in formats)


def test_cont01_no_facts_is_manual() -> None:
    for config in (None, SiteConfig()):
        result = run_audit(good_crawl(), config, ["CONT-01"])
        assert result.findings == []
        assert [m.description for m in result.manual] == [
            "No facts configured in the site config; check by hand"
        ]


def test_cont01_conflict_flags_each_page_and_quotes_both() -> None:
    crawl, config = FAIL["CONT-01"]()
    findings = findings_for(crawl, config, "CONT-01")
    assert {f.url for f in findings} == {HOME, f"{ORIGIN}/faq"}
    for f in findings:
        assert "15 delivery" in f.evidence and "15 purchase" in f.evidence


def test_cont01_expected_value_flags_only_wrong_pages() -> None:
    crawl, _ = FAIL["CONT-01"]()
    findings = findings_for(crawl, facts_config("15 delivery"), "CONT-01")
    assert [f.url for f in findings] == [f"{ORIGIN}/faq"]
    assert "15 days from purchase" in findings[0].evidence


def test_fact_pattern_must_be_a_regex() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SiteConfig.model_validate({"facts": [{"name": "x", "pattern": "(unclosed"}]})


def test_cont02_misspelling_and_brand_spelling() -> None:
    config = SiteConfig(brand="Acme Optics", brand_wrong_spellings=["Acme optics"])
    body = "<p>You will recieve your Acme optics filter soon.</p><p>Acme Optics</p>"
    findings = findings_for(good_crawl(pages=with_home_body(body)), config, "CONT-02")
    evidence = " ".join(f.evidence for f in findings)
    assert "'recieve' → 'receive'" in evidence
    assert "Wrong brand spelling 'Acme optics'" in evidence
    assert len(findings) == 2


def test_cont02_no_duplicate_across_separate_elements() -> None:
    body = "<h2>Shipping</h2><p>Shipping is free.</p>"
    assert findings_for(good_crawl(pages=with_home_body(body)), None, "CONT-02") == []


def test_cont03_near_duplicates() -> None:
    from sitekit import HOME_COPY

    b = f"{ORIGIN}/b"
    pages = [
        *good_pages(),
        page(
            b,
            html(
                url=b,
                title="Copy page of the example test shop",
                body=f"<p>{HOME_COPY} Extra.</p>",
            ),
        ),
    ]
    [finding] = findings_for(good_crawl(pages=pages), None, "CONT-03")
    assert finding.url == b and f"Near-duplicate of {HOME}" in finding.evidence


def test_old_crawl_without_text_fields() -> None:
    body = (
        '<img src="/x.webp" width="1" height="1">'
        "<p>Returns accepted 15 days from delivery.</p>"
    )
    data = good_crawl(pages=with_home_body(body)).model_dump(mode="json")
    for p in data["pages"]:
        if p["html"]:
            for key in ("text", "main_text", "question_headings"):
                p["html"].pop(key)
    old = Crawl.model_validate_json(json.dumps(data))
    assert old.pages[0].html is not None and old.pages[0].html.text == ""
    result = run_audit(old, facts_config(), ["CONT-01", "CONT-02", "CONT-03", "IMG-01"])
    rules_hit = {f.rule_id for f in result.findings}
    assert rules_hit == {"IMG-01"}
    assert {m.rule_id for m in result.manual} == {"CONT-01", "CONT-02", "CONT-03"}


def test_parse_visible_text_and_image_fields() -> None:
    from pss_seo_inspector.parse import parse_html

    parsed = parse_html(
        "<html><head><title>T</title><style>p{}</style></head><body>"
        '<nav><a href="/"><img src="/l.png" alt=""></a> Menu</nav>'
        '<main><h2>Why us?</h2><img src="/h.png" sizes="50vw" fetchpriority="high">'
        "<p>15 days from <b>delivery</b></p><script>var x</script></main>"
        "<footer>Foot</footer></body></html>",
        f"{ORIGIN}/",
    )
    assert parsed.text == "Menu\nWhy us?\n15 days from\ndelivery\nFoot"
    assert parsed.main_text == "Why us?\n15 days from\ndelivery"
    assert parsed.question_headings == ["Why us?"]
    logo, hero = parsed.images
    assert (logo.index, logo.landmark, logo.link_text) == (0, "nav", "")
    assert (hero.index, hero.landmark, hero.link_text) == (1, "", None)
    assert (hero.sizes, hero.fetchpriority) == ("50vw", "high")


# -- review fixes ------------------------------------------------------------


def _sd01_high(pages: list) -> list[str]:  # type: ignore[type-arg]
    findings = findings_for(good_crawl(pages=pages), None, "SD-01")
    return [f.evidence for f in findings if f.severity != Severity.low]


def test_sd01_shopify_product_group_and_main_entity() -> None:
    variant = {k: v for k, v in product().items() if k != "@context"}
    group = {
        "@context": "https://schema.org",
        "@type": "ProductGroup",
        "name": "AIM Filter",
        "productGroupID": "1",
        "variesBy": ["https://schema.org/size"],
        "hasVariant": [variant, {**variant, "sku": "AIM-53"}],
    }
    assert _sd01_high(product_page(group)) == []
    web_page = {"@type": "ItemPage", "mainEntity": variant}
    assert _sd01_high(product_page(web_page)) == []


@pytest.mark.parametrize(
    "node",
    [
        {
            "@graph": [
                {**product(), "offers": {"@id": "#offer"}},
                {**FULL_OFFER, "@id": "#offer"},
            ]
        },
        product(
            offers={"@type": "AggregateOffer", "lowPrice": 10, "priceCurrency": "USD"}
        ),
        product(
            offers={
                "@type": "Offer",
                "availability": "https://schema.org/InStock",
                "url": A,
                "priceSpecification": {
                    "@type": "UnitPriceSpecification",
                    "price": 49,
                    "priceCurrency": "USD",
                },
            }
        ),
    ],
    ids=["id-reference", "aggregate-low-price", "price-specification"],
)
def test_sd01_offer_shapes(node: dict[str, object]) -> None:
    assert not any("missing required" in e for e in _sd01_high(product_page(node)))


def test_sd01_numeric_prices_compare_by_value() -> None:
    app = product(offers={**FULL_OFFER, "price": 49.0})
    [dup] = _sd01_high(product_page(product(), app))
    assert "price differs" not in dup


def test_sd07_offer_level_policies() -> None:
    offer = {
        **FULL_OFFER,
        "hasMerchantReturnPolicy": {
            "@type": "MerchantReturnPolicy",
            "returnPolicyCountry": "US",
        },
        "shippingDetails": {"@type": "OfferShippingDetails"},
    }
    pages = product_page(product(offers=offer))
    assert findings_for(good_crawl(pages=pages), None, "SD-07") == []


def test_sd08_nested_website_ignored_and_identical_blocks_count_twice() -> None:
    site = {"@type": "WebSite", "name": "A", "alternateName": "a", "url": HOME}
    nested = ld({"@type": "WebPage", "isPartOf": site})
    assert (
        findings_for(good_crawl(pages=with_home_body(ld(site) + nested)), None, "SD-08")
        == []
    )
    findings = findings_for(
        good_crawl(pages=with_home_body(ld(site) + ld(site))), None, "SD-08"
    )
    assert [f.evidence for f in findings] == [
        "2 WebSite nodes on the home page: 'A', 'A'"
    ]


def test_sd04_entities_and_old_crawl() -> None:
    faq = {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": "Can I return &amp; exchange?",
                "acceptedAnswer": {"@type": "Answer", "text": "Yes &mdash; 15 days."},
            }
        ],
    }
    body = "<h2>Can I return &amp; exchange?</h2><p>Yes &mdash; 15 days.</p>"
    crawl = good_crawl(pages=product_page(faq, body=body))
    assert findings_for(crawl, None, "SD-04") == []
    headings = "".join(f"<h2>Question {n}?</h2>" for n in range(3))
    data = good_crawl(pages=with_home_body(headings)).model_dump(mode="json")
    for p in data["pages"]:
        p["html"].pop("text")
    result = run_audit(Crawl.model_validate_json(json.dumps(data)), None, ["SD-04"])
    assert result.findings == [] and len(result.manual) == 2


def test_parse_skips_noscript_and_reads_lazy_sources() -> None:
    from pss_seo_inspector.parse import parse_html

    parsed = parse_html(
        "<html><body>"
        '<noscript><img src="/ns.jpg"></noscript>'
        '<template><img src="/tpl.jpg"></template>'
        '<img src="data:image/gif;base64,R0lGOD" data-src="/lazy.jpg">'
        '<img src="" data-srcset="/set.jpg 100w, /set2.jpg 200w">'
        '<picture><source type="image/webp" srcset="/p.webp">'
        '<img src="/p.jpg"></picture>'
        "<div hidden>Secret</div><p>Shown</p>"
        "</body></html>",
        f"{ORIGIN}/",
    )
    assert [i.src for i in parsed.images] == [
        f"{ORIGIN}/lazy.jpg",
        f"{ORIGIN}/set.jpg",
        f"{ORIGIN}/p.jpg",
    ]
    assert [i.picture_modern for i in parsed.images] == [False, False, True]
    assert parsed.text == "Shown"


def test_img02_sizes_px_wins_over_width_attribute() -> None:
    body = (
        '<img src="/t.webp" alt="t" width="1000" height="1000" '
        'srcset="/t1.webp 800w, /t2.webp 1000w" sizes="100px">'
    )
    [finding] = findings_for(good_crawl(pages=with_home_body(body)), None, "IMG-02")
    assert "800w for a 100px slot" in finding.evidence


def test_img02_without_probes_says_not_run() -> None:
    crawl = good_crawl(pages=with_home_body('<img src="/x.webp" alt="x">'))
    result = run_audit(crawl, None, ["IMG-02"])
    assert [m.description for m in result.manual] == [
        "Image probes were not run for this crawl (1 image(s)); re-crawl to check "
        "intrinsic sizes"
    ]


def test_img_rules_on_old_crawl_ask_for_recrawl() -> None:
    body = (
        '<img src="/x.webp" alt="x" width="1" height="1" loading="lazy" '
        'srcset="/x.webp 100w">'
    )
    data = good_crawl(pages=with_home_body(body)).model_dump(mode="json")
    for img in data["pages"][0]["html"]["images"]:
        for key in ("sizes", "fetchpriority", "index", "link_text", "landmark"):
            img.pop(key)
    old = Crawl.model_validate_json(json.dumps(data))
    result = run_audit(old, None, ["IMG-02", "IMG-03"])
    assert result.findings == []
    assert [m.description for m in result.manual if "older crawl" in m.description] == [
        "1 page(s) from an older crawl.json lack image data for the srcset/sizes "
        "check; re-crawl",
        "1 page(s) from an older crawl.json lack image data for the lazy LCP image "
        "check; re-crawl",
    ]


@pytest.mark.parametrize(
    ("src", "headers", "content_type", "flagged"),
    [
        ("/a.jpg", {"vary": "Accept"}, "image/jpeg", False),
        ("/b.jpg", {}, "image/webp", False),
        ("/img/c", {}, "image/jpeg", True),
    ],
)
def test_img03_probe_negotiation(
    src: str, headers: dict[str, str], content_type: str, flagged: bool
) -> None:
    url = f"{ORIGIN}{src}"
    image_probe = probe("IMG-02", url, url, kind="image", headers=headers)
    image_probe.content_type = content_type
    crawl = good_crawl(
        pages=with_home_body(f'<img src="{src}" alt="x" width="1" height="1">'),
        probes=add_probe(image_probe),
    )
    findings = findings_for(crawl, None, "IMG-03")
    assert bool(findings) is flagged
    if flagged:
        assert "Served as image/jpeg" in findings[0].evidence


def test_cont02_groups_identical_evidence_across_pages() -> None:
    footer = "<footer><p>We recieve orders daily.</p></footer>"
    pages = [
        page(HOME, html(url=HOME, title="Home page of the example shop", body=footer)),
        page(A, html(url=A, title="About page of the example shop", body=footer)),
    ]
    [finding] = findings_for(good_crawl(pages=pages), None, "CONT-02")
    assert finding.url == HOME and finding.evidence.endswith("(+1 more pages)")
    assert len(finding.evidence) <= 300


def test_cont03_redirect_source_is_not_its_own_duplicate() -> None:
    target = good_pages()[1]
    old = target.model_copy(update={"url": f"{ORIGIN}/old-a"})
    crawl = good_crawl(pages=[*good_pages(), old])
    assert findings_for(crawl, None, "CONT-03") == []
