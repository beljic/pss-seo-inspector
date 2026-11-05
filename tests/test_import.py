"""import_pages: audit pages saved from the user's browser, with no network."""

from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sitekit import FakeSite, fixture_text

from pss_seo_inspector import server
from pss_seo_inspector.importer import (
    MAX_FILE_BYTES,
    MAX_FILES,
    ImportPagesError,
    import_pages,
)
from pss_seo_inspector.rules.base import all_rules

TODAY = date(2026, 10, 12)
SITE = "https://shop.test"


def _save(tmp: Path, name: str, text: str) -> str:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _entry(
    url: str,
    *,
    status: int = 200,
    method: str = "GET",
    mime: str = "text/html; charset=utf-8",
    body: str = "",
    headers: dict[str, str] | None = None,
    resource_type: str = "document",
    encoding: str | None = None,
) -> dict[str, Any]:
    content: dict[str, Any] = {"mimeType": mime, "text": body}
    if encoding:
        content["encoding"] = encoding
    return {
        "_resourceType": resource_type,
        "request": {"method": method, "url": url, "headers": []},
        "response": {
            "status": status,
            "headers": [
                {"name": k, "value": v}
                for k, v in {"Content-Type": mime, **(headers or {})}.items()
            ],
            "content": content,
        },
    }


def _har(tmp: Path, *entries: dict[str, Any]) -> str:
    path = tmp / "session.har"
    path.write_text(json.dumps({"log": {"version": "1.2", "entries": list(entries)}}))
    return str(path)


def test_html_import_takes_url_from_canonical_and_detects_platform(
    tmp_path: Path,
) -> None:
    home = _save(tmp_path, "home.html", fixture_text("shop", "home.html"))
    product = _save(tmp_path, "product.html", fixture_text("shop", "product.html"))
    crawl, skipped = import_pages(SITE, [home, product], today=TODAY)
    assert skipped == []
    assert crawl.crawl_id == "shop.test/2026-10-12-import"
    assert crawl.source == "import"
    assert crawl.platform == "shopify"
    assert [p.url for p in crawl.pages] == [
        "https://shop.test/",
        "https://shop.test/products/aim-filter",
    ]
    assert all(p.status == 200 and p.html is not None for p in crawl.pages)
    assert crawl.pages[0].source == "start"


def test_explicit_mapping_is_used_when_the_file_has_no_canonical(
    tmp_path: Path,
) -> None:
    path = _save(tmp_path, "x.html", "<html><head><title>X</title></head></html>")
    with pytest.raises(ImportPagesError, match="no page"):
        import_pages(SITE, [path], today=TODAY)
    crawl, _ = import_pages(SITE, [path], urls={path: f"{SITE}/x"}, today=TODAY)
    assert crawl.pages[0].url == "https://shop.test/x"


def test_wrong_host_file_is_rejected(tmp_path: Path) -> None:
    other = fixture_text("shop", "about.html").replace("shop.test", "other.test")
    good = _save(tmp_path, "home.html", fixture_text("shop", "home.html"))
    bad = _save(tmp_path, "about.html", other)
    crawl, skipped = import_pages(SITE, [good, bad], today=TODAY)
    assert [p.url for p in crawl.pages] == ["https://shop.test/"]
    assert len(skipped) == 1 and "not on shop.test" in skipped[0]


def test_saved_challenge_page_is_skipped(tmp_path: Path) -> None:
    good = _save(tmp_path, "home.html", fixture_text("shop", "home.html"))
    wall = _save(tmp_path, "wall.html", fixture_text("challenge", "cloudflare.html"))
    crawl, skipped = import_pages(
        SITE, [good, wall], urls={wall: f"{SITE}/pages/about"}, today=TODAY
    )
    assert len(crawl.pages) == 1
    assert crawl.blocked_count == 1
    assert "Cloudflare challenge" in skipped[0]


def test_har_import_keeps_only_get_200_documents_on_the_host(tmp_path: Path) -> None:
    home = fixture_text("shop", "home.html")
    product = fixture_text("shop", "product.html")
    har = _har(
        tmp_path,
        _entry(f"{SITE}/", body=home, headers={"X-Shopid": "1"}),
        _entry(
            f"{SITE}/products/aim-filter",
            body=base64.b64encode(product.encode()).decode(),
            encoding="base64",
        ),
        _entry(
            f"{SITE}/pages/about",
            status=403,
            body=fixture_text("challenge", "cloudflare.html"),
            headers={"cf-mitigated": "challenge"},
        ),
        _entry(f"{SITE}/cart/add", method="POST", body=home),
        _entry(f"{SITE}/hero.jpg", mime="image/jpeg", resource_type="image"),
        _entry("https://cdn.other.test/", body=home),
        _entry(f"{SITE}/old", status=301),
    )
    crawl, _ = import_pages(SITE, [har], today=TODAY)
    assert [p.url for p in crawl.pages] == [
        "https://shop.test/",
        "https://shop.test/products/aim-filter",
    ]
    assert crawl.pages[0].headers["x-shopid"] == "1"
    assert crawl.pages[1].html is not None
    assert crawl.pages[1].html.og.get("og:url") == f"{SITE}/products/aim-filter"
    assert crawl.blocked_count == 1
    assert crawl.bot_protection == "Cloudflare challenge"


def test_path_validation(tmp_path: Path) -> None:
    with pytest.raises(ImportPagesError, match="not found"):
        import_pages(SITE, [str(tmp_path / "missing.html")], today=TODAY)
    with pytest.raises(ImportPagesError, match="at most"):
        import_pages(SITE, ["x.html"] * (MAX_FILES + 1), today=TODAY)
    big = tmp_path / "big.html"
    with big.open("wb") as fh:
        fh.truncate(MAX_FILE_BYTES + 1)
    with pytest.raises(ImportPagesError, match="larger than"):
        import_pages(SITE, [str(big)], today=TODAY)
    txt = _save(tmp_path, "notes.txt", "hello")
    with pytest.raises(ImportPagesError, match=r"\.html"):
        import_pages(SITE, [txt], today=TODAY)
    link = tmp_path / "link.html"
    link.symlink_to(tmp_path / "notes.txt")
    with pytest.raises(ImportPagesError, match="symbolic link"):
        import_pages(SITE, [str(link)], today=TODAY)
    home = _save(tmp_path, "home.html", fixture_text("shop", "home.html"))
    with pytest.raises(ImportPagesError, match="not in paths"):
        import_pages(SITE, [home], urls={"/etc/hosts": SITE}, today=TODAY)


async def _call(name: str, args: dict[str, Any]) -> Any:
    from mcp import Client

    async with Client(server.mcp) as client:
        return await client.call_tool(name, args)


@pytest.mark.anyio
async def test_import_then_audit_runs_offline_and_marks_live_rules_not_checked(
    monkeypatch: pytest.MonkeyPatch, isolated_dirs: Path
) -> None:
    no_network = FakeSite(block_network=True).transport()
    monkeypatch.setattr(server, "transport", no_network)
    monkeypatch.setattr(server, "psi_transport", no_network)
    monkeypatch.setattr(server, "today", TODAY)
    home = _save(isolated_dirs, "home.html", fixture_text("shop", "home.html"))

    imported = await _call("import_pages", {"site_url": SITE, "paths": [home]})
    assert imported.is_error is False, imported
    data = imported.structured_content
    assert data["crawl_id"] == "shop.test/2026-10-12-import"
    assert data["page_count"] == 1

    audited = await _call("audit_site", {"crawl_id": data["crawl_id"]})
    assert audited.is_error is False, audited
    out = audited.structured_content
    live = {r.id for r in all_rules() if r.scope == "site" or r.probes is not None}
    live.add("IDX-14")
    applicable = {r.id for r in all_rules() if r.applies("shopify")}
    assert live & applicable <= set(out["not_checked"])
    assert not set(out["not_checked"]) & set(out["passed"])
    assert "PERF-01" in out["not_checked"]
    assert out["pagespeed_urls_checked"] == 0
    report = (
        isolated_dirs / "reports" / "shop.test" / "2026-10-12-import" / "report.md"
    ).read_text()
    assert "## Not checked (imported pages only)" in report
