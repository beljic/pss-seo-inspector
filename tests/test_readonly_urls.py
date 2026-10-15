"""Known GET action endpoints must never reach even a mocked transport."""

from __future__ import annotations

from datetime import date

import pytest
from sitekit import FakeClock, FakeSite, html

from pss_seo_inspector.crawler import crawl_site
from pss_seo_inspector.engine import run_audit
from pss_seo_inspector.fetcher import Fetcher

ORIGIN = "https://safe.test"
ACTION_PATHS = [
    "/?add-to-cart=123",
    "/?add_to_cart=123",
    "/?%61dd-to-cart=123",
    "/?%2561dd-to-cart=123",
    "/?remove_item=abc",
    "/?wc-ajax=add_to_cart",
    "/?action=delete&id=42",
    "/?action%5B%5D=add_to_cart",
    "/?task=cart.add&id=42",
    "/cart/add?id=42",
    "/cart/add.js?id=42",
    "/cart/change.json?id=42",
    "/cart/42:1",
    "/CART/ADD?id=42",
    "/cart/%61dd?id=42",
    "/cart/%2561dd?id=42",
    "/checkout/cart/add/product/42",
    "/index.php/checkout/cart/delete/id/42",
    "/wishlist/index/add/product/42",
    "/customer/account/logout",
    "/wp-login.php?action=logout",
    "/wp-json/wc/store/v1/cart/add-item",
    "/?rest_route=%2Fwc%2Fstore%2Fv1%2Fcart%2Fadd-item",
    "/?rest_route=%252Fwc%252Fstore%252Fv1%252Fcart%252Fadd-item",
    "/index.php?controller=cart&add=1&id_product=123&qty=1",
    "/index.php?controller=cart&delete=1&id_product=123",
    "/en/cart/42:1",
    "/default/checkout/cart/add/product/42",
    "/account/unsubscribe?token=abc",
]


@pytest.mark.anyio
@pytest.mark.parametrize("path", ACTION_PATHS)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_action_urls_never_reach_transport(path: str, method: str) -> None:
    site = FakeSite()
    async with Fetcher(transport=site.transport(), clock=FakeClock()) as fetcher:
        result = await fetcher.get(ORIGIN + path, method=method)  # type: ignore[arg-type]
    assert not site.requests
    assert result.resource.status is None
    assert result.resource.error is not None
    assert result.resource.error.startswith("read-only policy:")


@pytest.mark.anyio
@pytest.mark.parametrize("path", ACTION_PATHS)
async def test_redirect_to_action_url_is_never_followed(path: str) -> None:
    site = FakeSite()
    site.redirect(ORIGIN + "/old", ORIGIN + path)
    async with Fetcher(transport=site.transport(), clock=FakeClock()) as fetcher:
        result = await fetcher.get(ORIGIN + "/old")
    assert ORIGIN + path not in [url for _, url in site.requests]
    assert result.resource.error is not None
    assert result.resource.error.startswith("read-only policy:")
    assert result.resource.chain[0].location == ORIGIN + path


@pytest.mark.anyio
@pytest.mark.parametrize("path", ACTION_PATHS)
async def test_robots_redirect_cannot_bypass_action_guard(path: str) -> None:
    site = FakeSite()
    site.redirect(ORIGIN + "/robots.txt", ORIGIN + path)
    async with Fetcher(transport=site.transport(), clock=FakeClock()) as fetcher:
        robots, _ = await fetcher.robots(ORIGIN)
    assert site.requests == [("GET", ORIGIN + "/robots.txt")]
    assert robots.error is not None and robots.error.startswith("read-only policy:")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/cart", "/cart/", "/checkout/cart", "/customer/account/login",
        "/products/cart-bag", "/pages/add-to-cart-guide",
        "/collections/sale?sort_by=price-ascending&filter.v.availability=1",
        "/products/shirt?variant=42", "/?page=2", "/?action=view",
        "/products/subscribe", "/collections/delete",
        "/collections/cart/bags?page=2",
        "/index.php?controller=cart", "/?controller=category&add=1",
    ],
)
async def test_read_only_catalog_urls_still_fetch(path: str) -> None:
    site = FakeSite()
    url = ORIGIN + path
    site.add(url, html(url=url))
    async with Fetcher(transport=site.transport(), clock=FakeClock()) as fetcher:
        result = await fetcher.get(url)
    assert result.resource.status == 200
    assert ("GET", url) in site.requests


@pytest.mark.anyio
async def test_crawl_records_blocked_action_as_manual_without_requesting_it() -> None:
    site = FakeSite()
    bad = ORIGIN + "/?add-to-cart=123"
    site.add(
        ORIGIN + "/",
        html(url=ORIGIN + "/", body='<a href="/?add-to-cart=123">Add</a>'),
    )
    crawl = await crawl_site(
        ORIGIN, transport=site.transport(), clock=FakeClock(), today=date(2026, 10, 12)
    )
    assert bad not in [url for _, url in site.requests]
    audit = run_audit(crawl, None, ["IDX-05"])
    assert not audit.findings
    assert any("read-only policy" in item.description for item in audit.manual)


@pytest.mark.anyio
async def test_blocked_redirect_manual_check_identifies_final_destination() -> None:
    site = FakeSite()
    site.add(ORIGIN + "/", html(url=ORIGIN + "/", body='<a href="/old">Old</a>'))
    bad = ORIGIN + "/cart/add?id=123"
    site.redirect(ORIGIN + "/old", bad)
    crawl = await crawl_site(
        ORIGIN, transport=site.transport(), clock=FakeClock(), today=date(2026, 10, 12)
    )
    assert bad not in [url for _, url in site.requests]
    assert ("GET", ORIGIN + "/old") in site.requests
    audit = run_audit(crawl, None, ["IDX-05"])
    assert not audit.findings
    assert any(
        f"destination not requested: {bad}" in item.description
        for item in audit.manual
    )


@pytest.mark.anyio
async def test_non_readonly_method_is_rejected_at_runtime() -> None:
    site = FakeSite()
    async with Fetcher(transport=site.transport(), clock=FakeClock()) as fetcher:
        result = await fetcher.get(ORIGIN + "/", method="POST")  # type: ignore[arg-type]
    assert not site.requests
    assert result.resource.error is not None
    assert result.resource.error.startswith("read-only policy:")
