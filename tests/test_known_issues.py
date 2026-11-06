"""Regression tests for docs/KNOWN-ISSUES.md (found on real audits)."""

from __future__ import annotations

import struct

import httpx2
import pytest
from sitekit import ORIGIN, FakeClock, good_crawl, good_probes, html, page

from pss_seo_inspector.engine import run_audit
from pss_seo_inspector.fetcher import Fetcher
from pss_seo_inspector.imagesize import image_size
from pss_seo_inspector.models import Probe
from pss_seo_inspector.pagespeed import PageSpeedResult
from pss_seo_inspector.urlutil import is_public_host

PERF = [f"PERF-0{i}" for i in range(1, 8)]


# -- 2 + 3: META-05 width unknown --------------------------------------------


def test_meta05_unreadable_width_is_one_manual_per_image() -> None:
    pages = [
        page(url, html(url=url, og_width=""))
        for url in (f"{ORIGIN}/", f"{ORIGIN}/a", f"{ORIGIN}/b")
    ]
    probes = [
        p.model_copy(update={"image_width": None, "content_type": "image/png"})
        if p.rule_id == "META-05"
        else p
        for p in good_probes()
    ]
    result = run_audit(good_crawl(pages=pages, probes=probes), None, ["META-05"])
    manual = [m.description for m in result.manual]
    assert len(manual) == 1, manual
    assert "3 page(s)" in manual[0]
    assert "format not recognised" in manual[0] and "image/png" in manual[0]


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def test_image_size_reads_avif() -> None:
    ispe = _box(b"ispe", b"\x00\x00\x00\x00" + struct.pack(">II", 1600, 900))
    meta = _box(b"meta", b"\x00\x00\x00\x00" + _box(b"iprp", _box(b"ipco", ispe)))
    avif = _box(b"ftyp", b"avif\x00\x00\x00\x00mif1avif") + meta
    assert image_size(avif) == (1600, 900)


# -- 4: PageSpeed Insights ----------------------------------------------------


@pytest.mark.parametrize(
    ("host", "public"),
    [
        ("localhost", False),
        ("shop.local", False),
        ("app.localhost", False),
        ("127.0.0.1", False),
        ("[::1]", False),
        ("192.168.1.10", False),
        ("10.0.0.5", False),
        ("example.com", True),
        ("8.8.8.8", True),
    ],
)
def test_is_public_host(host: str, public: bool) -> None:
    assert is_public_host(host) is public


def test_psi_on_non_public_host_is_one_note_and_not_a_pass() -> None:
    crawl = good_crawl()
    crawl.host = "shop.local"
    result = run_audit(crawl, None, PERF)
    assert len(result.manual) == 1
    assert "cannot reach" in result.manual[0].description
    assert set(result.not_checked) == set(PERF)
    assert not set(result.passed) & set(PERF)


def test_psi_failure_is_one_note_per_url_not_per_rule() -> None:
    crawl = good_crawl()
    crawl.pagespeed_results = [
        PageSpeedResult(url=f"{ORIGIN}/", strategy="mobile", error="HTTP 429"),
        PageSpeedResult(url=f"{ORIGIN}/a", strategy="mobile", error="HTTP 429"),
    ]
    result = run_audit(crawl, None, PERF)
    notes = [m.description for m in result.manual]
    assert len(notes) == 2, notes
    assert set(result.not_checked) == set(PERF)


# -- 5: robots.txt unreachable is not "blocked by robots.txt" ----------------


@pytest.mark.anyio
async def test_unreachable_robots_reports_connection_error() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("Name or service not known")

    fetcher = Fetcher(transport=httpx2.MockTransport(refuse), clock=FakeClock())
    res = (await fetcher.get("https://www.nowhere.test/")).resource
    await fetcher.aclose()
    assert res.robots_blocked  # disallow-all semantics unchanged
    assert res.error is not None and res.error.startswith("could not connect:")


def test_idx09_says_could_not_connect() -> None:
    url = "https://www.ex.test/"
    probes = [
        *good_probes(),
        Probe(
            rule_id="IDX-09",
            key=url,
            url=url,
            final_url=url,
            kind="status",
            robots_blocked=True,
            error="could not connect: ConnectError: Name or service not known",
        ),
    ]
    result = run_audit(good_crawl(probes=probes), None, ["IDX-09"])
    notes = [m.description for m in result.manual]
    assert any("could not connect" in n for n in notes), notes
    assert not any("blocked by robots.txt" in n for n in notes)

