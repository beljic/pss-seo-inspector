# MCP setup

pss-seo-inspector exposes five typed MCP tools over stdio. Every tool takes
typed arguments; none of them accepts a shell command, a raw request method, or
an arbitrary file path. Unknown arguments are rejected before any request is
made. All examples below are synthetic (`example.test`).

| Tool | Arguments | What it returns |
|---|---|---|
| `crawl_site` | `url`, `max_pages?` (200), `rate?` (2.0) | `crawl_id` (`<domain>/<YYYY-MM-DD>`), URL count, platform, sitemap-only and crawl-only URL lists; saves `crawl.json` |
| `audit_site` | `url` **or** `crawl_id`, `rules?`, `site_config?`, `max_pages?`, `rate?` | Runs the rules (offline when given a `crawl_id`), runs PageSpeed Insights, writes the reports, returns the summary and `report_path` |
| `audit_url` | `url`, `rules?` | Quick single-page check with page-scope rules; writes nothing |
| `list_rules` | `platform?` | Rule ids, descriptions, severities, scopes, platforms |
| `get_findings` | `crawl_id`, `rule_id?`, `severity?` | Filtered findings from `findings.json` |

`audit_site` requires exactly one of `url` or `crawl_id`. With a `url` it
crawls first; with a `crawl_id` it re-audits a saved crawl without touching the
network for the crawl (PageSpeed Insights still calls Google's API).

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/beljic/pss-seo-inspector.git
cd pss-seo-inspector
uv sync
uv run pytest            # tests run offline against local fixtures
```

## Start the server

Configure your MCP client to launch the server from the checkout.

Claude Code:

```bash
claude mcp add pss-seo-inspector -- uv --directory /path/to/pss-seo-inspector run pss-seo-inspector
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "pss-seo-inspector": {
      "command": "uv",
      "args": ["--directory", "/path/to/pss-seo-inspector", "run", "pss-seo-inspector"],
      "env": {
        "PSS_SEO_REPORTS_DIR": "/path/to/private/pss-seo-reports",
        "PSS_SEO_SITES_DIR": "/path/to/private/pss-seo-sites",
        "PSS_PAGESPEED_API_KEY": "optional-psi-key"
      }
    }
  }
}
```

The example paths are synthetic. Point `PSS_SEO_REPORTS_DIR` and
`PSS_SEO_SITES_DIR` at directories outside any public repository so crawl data
and private site configs are never published.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PSS_SEO_REPORTS_DIR` | `reports/` in the checkout | Where `report.md`, `findings.json`, `urls.csv`, `crawl.json` are written |
| `PSS_SEO_SITES_DIR` | `sites/` in the checkout | Where `<domain>.json` site configs are read from |
| `PSS_PAGESPEED_API_KEY` | unset | Optional Google PageSpeed Insights API key; raises the PSI rate limit |

The PSI key is read from the environment only. Do not pass it as a tool
argument and do not commit it.

## Site config (optional)

A site config holds per-site expectations and is read from
`PSS_SEO_SITES_DIR/<domain>.json`, or from any path passed as `site_config`.
All keys are optional. See [`sites/example.test.json`](../sites/example.test.json)
for a complete synthetic example.

```json
{
  "brand": "Acme Optics",
  "brand_wrong_spellings": ["Acme optics"],
  "product_name_pattern": "^[A-Za-z]+ Filter in Frame [0-9]+$",
  "noindex_urls": ["/pages/internal-tool"],
  "known_issues": [
    {"description": "Recheck duplicate product meta descriptions", "rule_ids": ["META-02"]}
  ],
  "pagespeed_urls": ["https://example.test/", "https://example.test/products/sample-filter"],
  "legacy_urls": ["https://old.example.test/page.html"],
  "facts": [
    {"name": "return window", "pattern": "(\\d+)\\s*days?\\s+from\\s+(delivery|purchase)", "expected": "15 delivery"}
  ]
}
```

- `brand` / `brand_wrong_spellings` — drive META-01 and CONT-02 brand checks.
- `product_name_pattern` — regex for META-03 product naming.
- `noindex_urls` — URLs expected to be noindex or blocked (IDX-01).
- `known_issues` — descriptions re-verified against the new findings.
- `pagespeed_urls` — the exact URLs to measure with PageSpeed Insights (capped
  at five). When omitted, the audit measures the home page plus up to four
  crawled pages.
- `legacy_urls` — old URLs that should redirect to live pages (IDX-10).
- `facts` — regex facts that must read the same on every page (CONT-01).

A real site config may contain private brand and business data. Keep it in a
directory outside any public repository (`PSS_SEO_SITES_DIR`); this repo tracks
only the synthetic example and ignores every other `sites/*.json`.

## PageSpeed Insights

`audit_site` measures performance with Google's PageSpeed Insights API (mobile
strategy) and adds PERF-01..07 findings:

| Rule | Metric | Needs improvement | Poor |
|---|---|---|---|
| PERF-01 | Performance score | 50–89 (high) | < 50 (critical) |
| PERF-02 | Largest Contentful Paint | 2500–4000 ms | > 4000 ms |
| PERF-03 | Cumulative Layout Shift | 0.10–0.25 | > 0.25 |
| PERF-04 | Interaction to Next Paint | 200–500 ms | > 500 ms |
| PERF-05 | First Contentful Paint | 1800–3000 ms | > 3000 ms |
| PERF-06 | Total Blocking Time | 200–600 ms | > 600 ms |
| PERF-07 | Time to First Byte | 800–1800 ms | > 1800 ms |

Without `PSS_PAGESPEED_API_KEY`, the public PSI rate limit applies (about 25
requests per day), so the audit measures at most five URLs. A failed PSI call
is logged and skipped; the audit still completes. PageSpeed results and
findings appear in `report.md` under **Performance (PageSpeed Insights)** and
in `findings.json`.

## Reports

`PSS_SEO_REPORTS_DIR/<domain>/<YYYY-MM-DD>/`:

- `report.md` — summary (≤10 lines), findings grouped by rule (most severe
  first) with URLs, evidence and the platform-specific fix, a Performance
  section, passed rules, needs-manual-check, platform-skipped rules, and known
  issues re-verified.
- `findings.json` — `[{rule_id, severity, url, evidence, fix}]`.
- `urls.csv` — one row per crawled URL with title/meta/canonical/robots stats.
- `crawl.json` — raw crawl data so rules re-run without network access.

A same-day re-run overwrites `<domain>/<date>/`.

## Rules

Severity scale: critical > high > medium > low > info. A rule file in
`src/pss_seo_inspector/rules/` that defines `Rule` objects is picked up
automatically. Regenerate the rule table in the README with:

```bash
uv run python -m pss_seo_inspector.docs
```

## Security boundary

pss-seo-inspector performs read-only requests only. Against the target site it
issues `GET`/`HEAD` and reads `robots.txt`; it never submits forms, logs in,
adds to carts, uploads, or sends any mutating HTTP method. The only external
service it calls is Google's PageSpeed Insights API, with a `GET` for a capped
set of URLs. There is no generic request tool, and unknown tool arguments are
rejected. See [the architecture document](ARCHITECTURE.md) for the diagrams and
the full read-only boundary.
