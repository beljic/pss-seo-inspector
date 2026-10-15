"""In-process MCP client tests for the five tools and the I/O matrix."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import pytest
from sitekit import FakeClock, FakeSite, html, shop_site

from pss_seo_inspector import server
from pss_seo_inspector.rules import base

TODAY = date(2026, 10, 12)


def _psi_transport() -> httpx2.MockTransport:
    """A transport that answers every PSI call with 429 (fast, no network)."""

    def _handle(request: httpx2.Request) -> httpx2.Response:
        body = b'{"error":{"code":429,"message":"rate limited"}}'
        return httpx2.Response(429, content=body)

    return httpx2.MockTransport(_handle)


# A poor-performance PSI response: every metric is over its "poor" threshold.
_POOR_PSI = json.dumps(
    {
        "lighthouseResult": {
            "categories": {"performance": {"score": 0.30}},
            "audits": {
                "largest-contentful-paint": {"numericValue": 6000.0},
                "cumulative-layout-shift": {"numericValue": 0.4},
                "experimental-interaction-to-next-paint": {"numericValue": 700.0},
                "first-contentful-paint": {"numericValue": 4000.0},
                "total-blocking-time": {"numericValue": 900.0},
                "server-response-time": {"numericValue": 2500.0},
            },
        }
    }
).encode()


def _psi_success_transport() -> httpx2.MockTransport:
    """A transport that answers every PSI call with a poor-performance 200."""

    def _handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=_POOR_PSI)

    return httpx2.MockTransport(_handle)


def use_site(
    monkeypatch: pytest.MonkeyPatch,
    site: FakeSite,
    *,
    psi: httpx2.MockTransport | None = None,
) -> None:
    monkeypatch.setattr(server, "transport", site.transport())
    monkeypatch.setattr(server, "psi_transport", psi or _psi_transport())
    monkeypatch.setattr(server, "clock", FakeClock())
    monkeypatch.setattr(server, "today", TODAY)


async def call(name: str, args: dict[str, Any]) -> Any:
    from mcp import Client

    async with Client(server.mcp) as client:
        return await client.call_tool(name, args)


@pytest.mark.anyio
async def test_crawl_then_audit_writes_four_files(
    monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path
) -> None:
    site = shop_site()
    use_site(monkeypatch, site)

    crawled = await call("crawl_site", {"url": "https://shop.test"})
    assert crawled.is_error is False
    data = crawled.structured_content
    assert data["crawl_id"] == "shop.test/2026-10-12"
    assert data["platform"] == "shopify"
    assert data["url_count"] > 5
    assert "https://shop.test/pages/orphan" in data["sitemap_only"]
    assert "https://shop.test/old-page" not in data["crawl_only"]

    audited = await call("audit_site", {"crawl_id": data["crawl_id"]})
    assert audited.is_error is False
    out = isolated_dirs / "reports" / "shop.test" / "2026-10-12"
    for name in ("report.md", "findings.json", "urls.csv", "crawl.json"):
        assert (out / name).is_file(), name
    assert audited.structured_content["report_path"] == str(out / "report.md")

    findings = json.loads((out / "findings.json").read_text())
    assert findings
    for f in findings:
        assert set(f) == {"rule_id", "severity", "url", "evidence", "fix"}
        assert f["url"].strip() and f["evidence"].strip()
    rules_hit = {f["rule_id"] for f in findings}
    assert {"IDX-05", "IDX-06", "IDX-07", "META-01", "IDX-04"} <= rules_hit
    [img01] = [f for f in findings if f["rule_id"] == "IMG-01"]
    assert (
        img01["url"] == "https://shop.test/"
        and '<img src="/hero.jpg"/>' in (img01["evidence"])
    )
    [sd01] = [
        f for f in findings if f["rule_id"] == "SD-01" and f["severity"] == "high"
    ]
    assert sd01["url"] == "https://shop.test/products/aim-filter"
    assert "offers.availability" in sd01["evidence"]
    assert '"@type":"Product"' in sd01["evidence"]

    header = (out / "urls.csv").read_text().splitlines()[0]
    assert header == (
        "url,status,canonical,robots,title,title_len,meta_desc_len,h1_count,"
        "imgs_missing_alt,jsonld_types"
    )
    report = (out / "report.md").read_text()
    summary = report.split("## Summary", 1)[1].split("## Findings", 1)[0]
    assert len([line for line in summary.splitlines() if line.strip()]) <= 10
    for section in (
        "## Passed",
        "## Needs manual check",
        "## Skipped",
        "## Known issues re-verified",
    ):
        assert section in report
    assert "MAG-01" in report.split("## Skipped", 1)[1]


@pytest.mark.anyio
async def test_offline_reaudit_gives_same_findings(
    monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path
) -> None:
    site = shop_site()
    use_site(monkeypatch, site)
    first = await call("audit_site", {"url": "https://shop.test"})
    assert first.is_error is False
    out = isolated_dirs / "reports" / "shop.test" / "2026-10-12" / "findings.json"
    before = json.loads(out.read_text())

    site.block_network = True
    site.requests.clear()
    second = await call("audit_site", {"crawl_id": "shop.test/2026-10-12"})
    assert second.is_error is False
    # PSI calls go to googleapis.com, not the crawled site — filter them out
    site_requests = [
        r for r in site.requests if "googleapis.com" not in r[1]
    ]
    assert site_requests == []
    assert json.loads(out.read_text()) == before


@pytest.mark.anyio
async def test_get_findings_filters_and_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_site(monkeypatch, shop_site())
    await call("audit_site", {"url": "https://shop.test"})
    result = await call(
        "get_findings",
        {"crawl_id": "shop.test/2026-10-12", "rule_id": "IDX-05", "severity": "high"},
    )
    assert result.is_error is False
    rows = result.structured_content["findings"]
    assert rows and all(
        r["rule_id"] == "IDX-05" and r["severity"] == "high" for r in rows
    )

    missing = await call("get_findings", {"crawl_id": "x/y"})
    assert missing.is_error is True
    assert "x/y" in missing.content[0].text


@pytest.mark.anyio
async def test_bad_site_config_is_a_tool_error(
    monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path
) -> None:
    use_site(monkeypatch, shop_site())
    (isolated_dirs / "sites" / "shop.test.json").write_text('{"brand": 5, "nope": 1}')
    result = await call("crawl_site", {"url": "https://shop.test"})
    assert result.is_error is True
    assert "validation error" in result.content[0].text

    (isolated_dirs / "sites" / "shop.test.json").write_text("{not json")
    result = await call("audit_site", {"url": "https://shop.test"})
    assert result.is_error is True
    assert "not valid JSON" in result.content[0].text


@pytest.mark.anyio
async def test_site_config_drives_rules(
    monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path
) -> None:
    use_site(monkeypatch, shop_site())
    config = isolated_dirs / "custom.json"
    config.write_text(
        json.dumps(
            {
                "brand": "Shop Test",
                "known_issues": [
                    {"description": "broken links", "rule_ids": ["IDX-05"]}
                ],
            }
        )
    )
    result = await call(
        "audit_site", {"url": "https://shop.test", "site_config": str(config)}
    )
    assert result.is_error is False
    report = Path(result.structured_content["report_path"]).read_text()
    assert "broken links — **still present**" in report
    assert "appears 2 times" in report or "Repeated title segment" in report


@pytest.mark.anyio
async def test_same_day_rerun_overwrites(
    monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path
) -> None:
    use_site(monkeypatch, shop_site())
    await call("crawl_site", {"url": "https://shop.test"})
    path = isolated_dirs / "reports" / "shop.test" / "2026-10-12" / "crawl.json"
    path.write_text(path.read_text().replace('"max_pages": 200', '"max_pages": 200 '))
    marker = path.read_text()
    second = await call("crawl_site", {"url": "https://shop.test", "max_pages": 3})
    assert second.is_error is False
    assert path.read_text() != marker
    assert json.loads(path.read_text())["max_pages"] == 3


@pytest.mark.anyio
async def test_unknown_platform_is_generic_with_generic_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FakeSite()
    site.add("https://gen.test/", html(url="https://gen.test/", title="Short"))
    use_site(monkeypatch, site)
    result = await call("audit_site", {"url": "https://gen.test/"})
    assert result.structured_content["platform"] == "generic"
    rows = (
        await call(
            "get_findings", {"crawl_id": "gen.test/2026-10-12", "rule_id": "META-01"}
        )
    ).structured_content
    from pss_seo_inspector.rules.base import get_rule

    rule = get_rule("META-01")
    assert rule is not None
    assert rows["findings"][0]["fix"] == rule.fix["generic"]
    assert set(result.structured_content["skipped"]) == {
        "SHOP-01",
        "SHOP-02",
        "SHOP-03",
        "MAG-01",
        "MAG-02",
        "WP-01",
    }


@pytest.mark.anyio
async def test_audit_url_quick_check(monkeypatch: pytest.MonkeyPatch) -> None:
    site = shop_site()
    use_site(monkeypatch, site)
    result = await call("audit_url", {"url": "https://shop.test/products/aim-filter"})
    assert result.is_error is False
    data = result.structured_content
    assert data["status"] == 200
    assert any(f["rule_id"] == "META-01" for f in data["findings"])
    assert not any(url.endswith("sitemap.xml") for _, url in site.requests)


@pytest.mark.anyio
async def test_closed_argument_surface_and_validation() -> None:
    result = await call("list_rules", {"platform": "shopify", "extra": 1})
    assert result.is_error is True
    assert "Unknown arguments: extra" in result.content[0].text
    both = await call("audit_site", {"url": "https://a.test", "crawl_id": "a.test/x"})
    assert both.is_error is True
    bad_rule = await call(
        "audit_site", {"crawl_id": "a.test/2026-01-01", "rules": ["NOPE-1"]}
    )
    assert bad_rule.is_error is True and "NOPE-1" in bad_rule.content[0].text


@pytest.mark.anyio
async def test_list_rules_by_platform() -> None:
    result = await call("list_rules", {"platform": "magento2"})
    ids = [r["id"] for r in result.structured_content["rules"]]
    assert "MAG-01" in ids and "SHOP-01" not in ids and "IDX-01" in ids
    every = await call("list_rules", {})
    assert len(every.structured_content["rules"]) == 54
    severity = {r["id"]: r["severity"] for r in every.structured_content["rules"]}
    assert [severity[i] for i in ("SD-04", "SD-05", "SD-06")] == ["info"] * 3


@pytest.mark.anyio
async def test_new_rule_module_is_listed_without_crawler_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plugin_dir = tmp_path / "plugin_rules"
    plugin_dir.mkdir()
    (plugin_dir / "zz_custom.py").write_text(
        "from pss_seo_inspector.models import Severity\n"
        "from pss_seo_inspector.rules.base import Rule\n"
        "CUSTOM = Rule(id='CUST-01', category='custom', severity=Severity.low,\n"
        "              description='Custom rule', check=lambda ctx: [],\n"
        "              fix={'generic': 'Do it.'})\n"
    )
    import pss_seo_inspector.rules as rules_pkg

    monkeypatch.setattr(rules_pkg, "__path__", [*rules_pkg.__path__, str(plugin_dir)])
    monkeypatch.setattr(base, "_REGISTRY", dict(base._REGISTRY))
    result = await call("list_rules", {})
    ids = [r["id"] for r in result.structured_content["rules"]]
    assert "CUST-01" in ids


def test_readme_rule_table_matches_registry() -> None:
    from pss_seo_inspector.docs import rule_table

    readme = (Path(__file__).parents[1] / "README.md").read_text()
    assert rule_table() in readme


@pytest.mark.anyio
async def test_pagespeed_success_adds_perf_findings_without_stale_manual(
    monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path
) -> None:
    use_site(monkeypatch, shop_site(), psi=_psi_success_transport())
    result = await call("audit_site", {"url": "https://shop.test"})
    assert result.is_error is False
    data = result.structured_content
    assert data["pagespeed_urls_checked"] >= 1

    out = isolated_dirs / "reports" / "shop.test" / "2026-10-12"
    findings = json.loads((out / "findings.json").read_text())
    perf_ids = {f["rule_id"] for f in findings if f["rule_id"].startswith("PERF-")}
    # A poor response trips every PERF rule.
    assert perf_ids == {f"PERF-0{i}" for i in range(1, 8)}

    # No stale "no data" manual note survives when PSI succeeded.
    report = (out / "report.md").read_text()
    assert "No PageSpeed Insights data available" not in report
    assert "## Performance (PageSpeed Insights)" in report
    assert all(
        "No PageSpeed Insights data available" not in m["description"]
        for m in data["manual"]
    )


@pytest.mark.anyio
async def test_pagespeed_not_forced_when_out_of_scope(
    monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path
) -> None:
    use_site(monkeypatch, shop_site(), psi=_psi_success_transport())
    result = await call(
        "audit_site", {"url": "https://shop.test", "rules": ["META-01"]}
    )
    assert result.is_error is False
    out = isolated_dirs / "reports" / "shop.test" / "2026-10-12"
    findings = json.loads((out / "findings.json").read_text())
    assert not any(f["rule_id"].startswith("PERF-") for f in findings)


@pytest.mark.anyio
async def test_pagespeed_all_failed_does_not_pass(
    monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path
) -> None:
    # Every PSI call returns 429 (the default _psi_transport). No metric is
    # measured, so no PERF rule may be reported as passed; each must leave a
    # manual note instead.
    use_site(monkeypatch, shop_site())
    result = await call("audit_site", {"url": "https://shop.test"})
    assert result.is_error is False
    data = result.structured_content

    perf_ids = {f"PERF-0{i}" for i in range(1, 8)}
    assert not any(rid in perf_ids for rid in data["passed"])

    manual_by_rule = {m["rule_id"] for m in data["manual"]}
    assert perf_ids <= manual_by_rule

    out = isolated_dirs / "reports" / "shop.test" / "2026-10-12"
    report = (out / "report.md").read_text()
    passed_section = report.split("## Passed", 1)[1].split("##", 1)[0]
    assert not any(rid in passed_section for rid in perf_ids)
