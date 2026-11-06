# Known issues (found while auditing real sites)

Each entry: what happened, evidence, proposed fix, status.

## 1. Crawl-delay made a crawl silently take very long — FIXED 2026-11-06

- **Found:** audit of a local dev store (`https://shop.local/`), `max_pages=100`.
  robots.txt had `Crawl-delay: 10`; the audit ran over 10 minutes with no output.
- **Cause:** the fetcher waits Crawl-delay before every request (pages, redirect
  hops, up to 500 link checks), which is correct, but nothing told the user.
- **Fix:** `Crawl.crawl_delay` stores the honoured delay; the crawler sends a
  progress message "robots.txt Crawl-delay Ns: up to ~X min of waiting"; the
  report summary and `crawl_site` output show it.
  Test: `test_crawl_delay_is_announced_and_reported` (fails without the fix).
- **Not done:** lowering the link-check cap when the delay is high.

## 2. META-05 manual check repeated once per page — FIXED 2026-11-06

- **Evidence:** 11 identical lines "Could not read the width of og:image
  …/logo.png" in "Needs manual check" (same image on 11 pages).
- **Fix:** `rules/meta.py` collects unreadable images and emits one manual entry
  per image URL with the page count.
  Test: `test_meta05_unreadable_width_is_one_manual_per_image`.

## 3. Image size reader did not know AVIF — FIXED 2026-11-06

- **Evidence:** the og:image above is AVIF bytes (`ftypavif`) served as
  `image/png`; the width was unknown and the message did not say why.
- **Fix:** `imagesize.py` reads AVIF width/height from the `ispe` box. When the
  format is still unknown, the META-05 note says "image format not recognised"
  and shows the Content-Type.
  Test: `test_image_size_reads_avif` (+ the META-05 test above).
- **Not done:** an explicit "Content-Type does not match the bytes" check (the
  probe does not keep the bytes); the note only says the bytes may differ.

## 4. PageSpeed Insights called for a host Google cannot reach — FIXED 2026-11-06

- **Evidence:** audit of `shop.local` called the PSI API for 5 URLs;
  all got HTTP 429, repeated for PERF-01…PERF-04 × 5 URLs = 20 manual entries.
- **Fix:** `urlutil.is_public_host` (localhost, `*.localhost`, `*.local`,
  non-global IPs); `server.py` skips PSI for such hosts. `engine.py` adds PSI
  notes once per audit (`rules/pagespeed.psi_notes`): one line for "Google
  cannot reach the non-public host", or one line per failed URL. With no
  measured URL, PERF rules go to "Not checked" (new report section), never
  "Passed". API key redaction unchanged.
  Tests: `test_is_public_host`, `test_psi_on_non_public_host_is_one_note_and_not_a_pass`,
  `test_psi_failure_is_one_note_per_url_not_per_rule`; updated
  `test_pagespeed_all_failed_does_not_pass` (it asserted the old per-rule notes).
- **Not done:** `*.test` is still treated as public — the test suite's fake
  sites use `shop.test` with a mocked PSI transport. The server-level skip has
  no own test (covered by `is_public_host` unit tests only).

## 5. "Blocked by robots.txt" when the host does not resolve — FIXED 2026-11-06

- **Evidence:** probes for `https://www.shop.local/` had no status and
  `robots_blocked=True`: robots.txt could not be fetched, which means
  "disallow all", and the note said "blocked by robots.txt for this crawler".
- **Fix:** `fetcher.py` keeps `robots_blocked=True` (semantics unchanged) and
  sets `error="could not connect: <error>"` when robots.txt had no status.
  IDX-09, IDX-10 and META-05 show that error instead of "blocked by robots.txt".
  Tests: `test_unreachable_robots_reports_connection_error`,
  `test_idx09_says_could_not_connect`.
- **Not done:** IDX-09 still probes `www.` for a host that is already a
  subdomain.

## 6. SD-01 / SD-03 only read JSON-LD, not microdata — OPEN

- **Evidence:** product pages with valid microdata (`itemtype="http://schema.org/Product"`
  with an `Offer`: name, image, sku, price, priceCurrency, availability, url) were
  reported as "Product page without Product JSON-LD" (high). Google accepts
  microdata and RDFa as well as JSON-LD.
- **Proposed fix:** parse microdata (`itemscope`/`itemtype`/`itemprop`) into the
  same structure the JSON-LD rules use, or at least report "Product microdata
  found, JSON-LD not present" at low severity instead of high. Also flag
  `itemprop` used without an enclosing `itemscope` (orphaned microdata, e.g. a
  breadcrumb trail with `itemprop="name"` but no `BreadcrumbList`).

## 7. Audit results from a page that returns a PHP fatal error — OPEN

- **Evidence:** a local store served `200` with a short body that started with
  "Fatal error: Uncaught Error: …" and no `<html>`. The audit reported it as a
  normal page (missing title, canonical, h1) and a random URL returning `200`
  as critical, which was really the same error.
- **Proposed fix:** detect error bodies (no `<html>`/`<body>`, starts with
  "Fatal error" / "Parse error" / "Uncaught") and report one "server error page"
  finding instead of running page rules on it.
