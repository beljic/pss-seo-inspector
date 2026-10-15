# pss-seo-inspector

Read-only, platform-aware technical SEO audit MCP server. It crawls any public
website with `GET`/`HEAD` requests only, detects the platform (Shopify,
Magento 2, WooCommerce, otherwise `generic`), runs a data-driven rule set, adds
Google PageSpeed Insights performance checks, and writes evidence-backed
reports.

See [the architecture document](docs/ARCHITECTURE.md) for the crawl-to-report
flow and the read-only security boundary.

See [the MCP setup guide](docs/MCP.md) for installing the server, configuring a
client, site configs, PageSpeed Insights, and the full tool reference.

What pss-seo-inspector can do:

- crawl politely: `GET`/`HEAD` only (plus `robots.txt`), User-Agent
  `pss-seo-inspector/1.0`, default 2 requests/second, robots.txt honoured with
  Google's matching rules (`*` and `$` wildcards, longest match wins);
- detect the platform from HTML, headers and asset paths, and apply shared plus
  platform-specific rules with platform-aware fix guidance;
- run 54 data-driven rules across indexing, metadata, structured data, images,
  content, and platform specifics, each finding carrying a URL and evidence
  (status/redirect chain or HTML snippet);
- measure Core Web Vitals and performance with Google PageSpeed Insights
  (PERF-01..07): performance score, LCP, CLS, INP, FCP, TBT, TTFB;
- re-audit a saved crawl offline, so rules re-run without touching the network;
- write a Markdown report, a findings JSON, a per-URL CSV, and the raw crawl.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/beljic/pss-seo-inspector.git
cd pss-seo-inspector
uv sync
uv run pytest            # tests run offline against local fixtures
```

Register the server with an MCP client as described in
[the MCP setup guide](docs/MCP.md). Reports are written to
`$PSS_SEO_REPORTS_DIR` (default `reports/` in this checkout) and site configs
are read from `$PSS_SEO_SITES_DIR` (default `sites/`). An optional PageSpeed
Insights API key is read from `$PSS_PAGESPEED_API_KEY`.

## Tools

| Tool | Arguments | Result |
|---|---|---|
| `crawl_site` | `url`, `max_pages?` (200), `rate?` (2.0) | `crawl_id` (`<domain>/<YYYY-MM-DD>`), URL count, platform, sitemap-only and crawl-only URL lists; saves `crawl.json` |
| `audit_site` | `url` **or** `crawl_id`, `rules?`, `site_config?`, `max_pages?`, `rate?` | Runs the rules (offline when given a `crawl_id`), runs PageSpeed Insights, writes the reports, returns the summary and `report_path` |
| `audit_url` | `url`, `rules?` | Quick single-page check with page-scope rules; writes nothing |
| `list_rules` | `platform?` | Rule ids, descriptions, severities, scopes, platforms |
| `get_findings` | `crawl_id`, `rule_id?`, `severity?` | Filtered findings from `findings.json` |

Full argument details, report layout, site-config schema, and the PageSpeed
Insights thresholds are in [the MCP setup guide](docs/MCP.md).

## Read-only guarantee

pss-seo-inspector is designed to read public web pages only. It issues
`GET`/`HEAD` requests to the target site plus `robots.txt`, and a `GET` to
Google's PageSpeed Insights API for a capped set of URLs. It never submits
forms, logs in, adds to carts, uploads, or sends any mutating HTTP method, and
it exposes no generic request tool. Unknown MCP tool arguments are rejected
before any request is made.

This guarantee applies to the standard, unmodified package installed from a
trusted source. A user who installs modified code is outside this guarantee.

## Privacy

Crawl data and reports may contain page content, titles, and URLs from real
sites; treat them as private. Real per-site configs may contain private brand
and business expectations. Keep both outside any public repository by pointing
`PSS_SEO_REPORTS_DIR` and `PSS_SEO_SITES_DIR` at private directories. This
repository ignores `reports/` and every `sites/*.json` except the synthetic
`sites/example.test.json`. The PageSpeed Insights API key is read from an
environment variable and must never be committed. All brands, domains, and
paths in this repository and its documentation are synthetic examples.

## Rules

Severity scale: critical > high > medium > low > info. A rule file in
`src/pss_seo_inspector/rules/` that defines `Rule` objects is picked up
automatically; the crawler needs no change. Regenerate this table with
`uv run python -m pss_seo_inspector.docs`.

<!-- rules:start -->
| ID | Severity | Applies to | Scope | Description |
|---|---|---|---|---|
| CONT-01 | medium | all | page | Facts from the site config (e.g. return window) read the same on every page |
| CONT-02 | low | all | page | No duplicated words, common misspellings or wrong brand spellings in page copy |
| CONT-03 | low | all | page | Indexable pages have ≥150 words of main content and are not near-duplicates of each other |
| IDX-01 | high | all | site | Utility pages (search, cart, checkout, account, configured URLs) are noindex or blocked by robots.txt |
| IDX-02 | critical | all | site | A random non-existent URL returns a real 404 and the 404 page is noindex |
| IDX-03 | high | all | page | Every indexable page has exactly one absolute, self-referencing canonical; parameter and duplicate URLs canonicalize to the clean URL |
| IDX-04 | medium | all | page | Internal links point to canonical URLs (count per page) |
| IDX-05 | high | all | site | Every internal link returns 200 directly or via one 301 (no 4xx/5xx, chains or loops) |
| IDX-06 | medium | all | page | URL hygiene in href/src: double slashes, trailing-slash mismatch, legacy .html 404s, uppercase, tracking and session parameters |
| IDX-07 | medium | all | site | Orphan pages: sitemap URLs that no crawled page links to |
| IDX-08 | high | all | site | Sitemap lists only 200, non-redirected, indexable, canonical URLs that robots.txt allows |
| IDX-09 | critical | all | site | Host and protocol variants (www/non-www, http/https) 301 to one canonical host |
| IDX-10 | medium | all | site | Legacy domains/URLs from the site config redirect to live 200 pages |
| IDX-11 | high | all | site | robots.txt health: reachable (2xx/4xx, not 5xx/401/403), ≤500 KiB, UTF-8, no unsupported directives, absolute Sitemap lines |
| IDX-12 | high | all | site | No URL is both robots-disallowed and noindex (the noindex is never seen) |
| IDX-13 | medium | all | site | Sitemap format: reachable, ≤50,000 URLs and ≤50 MB per file, absolute in-scope URLs, lastmod present and not in the future |
| IDX-14 | high | all | page | Canonical targets are 200, not redirected, indexable and not chained; HTML and Link-header canonicals agree |
| IDX-15 | medium | all | page | No mixed content: http resources or internal http links on https pages |
| IDX-16 | low | all | page | Internal links: no nofollow, no empty/generic anchor text, pages within 3 clicks of home |
| IDX-17 | medium | all | site | AI bots: search bots (OAI-SearchBot, Claude-SearchBot, PerplexityBot) not blocked; training-bot policy and llms.txt (info) |
| IMG-01 | medium | all | page | <img> has alt text (empty alt only for decorative images, not for linked, logo or product images) |
| IMG-02 | medium | all | page | Images not larger than 2x their declared width / sizes slot; srcset with w-descriptors has sizes |
| IMG-03 | low | all | page | Images in WebP/AVIF (or CDN-negotiated), with width/height; first content image not lazy-loaded |
| MAG-01 | high | magento2 | page | Magento 2: product and category pages have a canonical; product canonicals contain no category path |
| MAG-02 | medium | magento2 | site | Magento 2: no ___store/___from_store parameters in links; the same page is not served with and without the store code |
| META-01 | high | all | page | Title present, unique, ~30–60 chars; brand from the site config exactly once, correctly spelled; no repeated suffixes |
| META-02 | medium | all | page | Meta description present, unique, ~70–160 chars; duplicate clusters reported |
| META-03 | low | all | page | Product names match the naming pattern from the site config |
| META-04 | high | all | page | Exactly one non-empty <h1> per page (not just the logo) |
| META-05 | medium | all | page | og:title, og:description, og:url, twitter:card and a reachable og:image ≥1200px wide |
| META-06 | low | all | page | <html lang> set; hreflang present when several locales exist |
| META-07 | medium | all | page | Head hygiene: title, meta robots, canonical inside <head> and not duplicated; viewport, charset and doctype present |
| META-08 | low | all | page | Robots meta / X-Robots-Tag values valid; no ignored, expired or snippet-restricting directives |
| META-09 | medium | all | page | hreflang reciprocity: return links, self reference, x-default, valid codes, 200 targets |
| PERF-01 | high | all | site | PageSpeed Insights overall performance score ≥ 90 |
| PERF-02 | high | all | site | Largest Contentful Paint (LCP) ≤ 2500 ms |
| PERF-03 | medium | all | site | Cumulative Layout Shift (CLS) ≤ 0.1 |
| PERF-04 | medium | all | site | Interaction to Next Paint (INP) ≤ 200 ms |
| PERF-05 | medium | all | site | First Contentful Paint (FCP) ≤ 1800 ms |
| PERF-06 | medium | all | site | Total Blocking Time (TBT) ≤ 200 ms |
| PERF-07 | low | all | site | Time to First Byte (TTFB) ≤ 800 ms |
| SD-00 | high | all | page | Every JSON-LD block is valid JSON |
| SD-01 | high | all | page | Product JSON-LD: name, image, offers.price/priceCurrency/availability/url; recommended fields; one Product per page without conflicting values |
| SD-02 | medium | all | page | Home page has Organization/OnlineStore JSON-LD with name, url, logo, sameAs |
| SD-03 | low | all | page | Product and category pages have BreadcrumbList JSON-LD |
| SD-04 | info | all | page | FAQPage Q&A is visible on the page; FAQ-like headings without FAQPage (FAQ rich results retired 2026-05-07) |
| SD-05 | info | all | page | WebSite SearchAction present (sitelinks search box retired); target has the query placeholder |
| SD-06 | info | all | page | Retired rich-result markup present (FAQPage, ClaimReview, Course, EstimatedSalary, SpecialAnnouncement, Vehicle, Book actions, Dataset, ...) |
| SD-07 | medium | all | page | Return policy (hasMerchantReturnPolicy + returnPolicyCountry) and shipping (shippingDetails or Organization hasShippingService) declared |
| SD-08 | medium | all | page | Home page has exactly one WebSite node with name and url (alternateName recommended) |
| SHOP-01 | high | shopify | site | Shopify: /collections/vendors?q= and /collections/types?q= with random values return 404 or noindex |
| SHOP-02 | medium | shopify | site | Shopify: /collections/all, /blogs/*/tagged/* and .json/?view= alternates are not indexable and linked |
| SHOP-03 | medium | shopify | site | Shopify: robots.txt keeps the platform default Disallow lines and has no blanket 'Disallow: /' |
| WP-01 | medium | woocommerce | site | WordPress/WooCommerce: attachment pages do not return 200 HTML |
<!-- rules:end -->

## Development

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```
