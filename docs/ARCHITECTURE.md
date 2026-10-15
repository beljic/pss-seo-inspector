# pss-seo-inspector Architecture

This document describes the public, read-only architecture. All brands,
domains, and paths in the examples are synthetic (`example.test`,
`Acme Optics`).

## Crawl-to-report flow

```mermaid
flowchart LR
    P[User prompt] --> M[MCP typed tool]
    M --> V[Validate url / crawl_id, rules, limits]
    V --> SC[Optional site config]
    SC --> C[Polite crawler: GET/HEAD, robots.txt]
    C --> PD[Platform detection]
    PD --> RE[Data-driven rule engine]
    RE --> PSI[PageSpeed Insights client]
    PSI --> F

    RE --> F[Findings + evidence]
    F --> W[Report writer]
    W --> MD[report.md]
    W --> JS[findings.json]
    W --> CSV[urls.csv]
    W --> RAW[crawl.json]
```

The prompt becomes typed tool arguments; it never becomes a shell command or a
raw request to the target site. The crawler collects pages, redirects,
robots.txt and sitemaps once; rules then run over that data, so a re-audit from
a saved `crawl.json` needs no network access. PageSpeed Insights is the only
step that calls an external service (Google's PSI API) and only for a capped
set of URLs.

## Read-only boundary

```mermaid
flowchart TD
    U[Prompt or MCP client] --> T[Typed MCP schema, unknown arguments rejected]
    T --> F{Request type}
    F -->|target site| G["GET / HEAD only (+ robots.txt)"]
    F -->|PageSpeed| K[GET PSI API, mobile strategy]
    G --> RB[Honour robots.txt: UA pss-seo-inspector/1.0]
    RB --> RL[Rate limit: default 2 req/s]
    RL --> EV[Evidence-backed findings]
    K --> EV
    EV --> OUT[Reports under PSS_SEO_REPORTS_DIR]
```

The server never submits forms, logs in, adds to carts, or sends any mutating
HTTP method. It issues `GET`/`HEAD` to the target site plus `robots.txt`, and a
`GET` to the PageSpeed Insights endpoint. There is no generic request tool: a
caller cannot ask it to `POST`, upload, or run arbitrary URLs with arbitrary
methods. robots.txt is honoured with Google's matching rules, requests are rate
limited, and every finding carries a URL and the evidence behind it.

## Platform detection and rule scope

```mermaid
flowchart LR
    H[HTML + headers + asset paths] --> D{Markers}
    D -->|Shopify CDN / markers| S[shopify]
    D -->|Magento static paths| M[magento2]
    D -->|WooCommerce markers| WC[woocommerce]
    D -->|none| GN[generic]
    S --> R[Rule set: shared + platform rules]
    M --> R
    WC --> R
    GN --> R
    R --> FIX[Platform-aware fix text]
```

Rules declare the platforms they apply to and a scope (`page` or `site`).
A rule that does not apply to the detected platform is reported as skipped, not
as a failure. Fix guidance is platform-aware: a Shopify theme hint differs from
a Magento admin hint, and generic advice is used when the platform is unknown.

## PageSpeed Insights integration

```mermaid
sequenceDiagram
    participant E as Engine
    participant S as URL selector
    participant G as PSI API (googleapis.com)
    E->>S: crawl + site config
    alt pagespeed_urls set in site config
        S-->>E: those URLs (max 5)
    else not set
        S-->>E: home + up to 4 crawled pages (max 5)
    end
    loop each selected URL (sequential)
        E->>G: GET runPagespeed?strategy=mobile&category=performance
        G-->>E: Lighthouse metrics, or an error
        E->>E: on error, log a warning and skip (audit never crashes)
    end
    E->>E: PERF-01..07 turn metrics into findings
```

The PSI client uses the mobile strategy, a 30-second timeout per call, and an
optional API key from `PSS_PAGESPEED_API_KEY`. Without a key the public PSI
rate limit applies, so the selection is capped at five URLs. A failed PSI call
(network error, 429, malformed response) is logged and skipped; the rest of the
audit still completes and writes its report.

## Storage model

```mermaid
flowchart LR
    RD["PSS_SEO_REPORTS_DIR (default: reports/)"] --> CD["reports/&lt;domain&gt;/&lt;YYYY-MM-DD&gt;/"]
    CD --> C1[crawl.json]
    CD --> C2[findings.json]
    CD --> C3[report.md]
    CD --> C4[urls.csv]
    SD["PSS_SEO_SITES_DIR (default: sites/)"] --> SF["&lt;domain&gt;.json: optional expectations"]
```

Reports are written under `PSS_SEO_REPORTS_DIR` (default `reports/` in the
checkout), one dated folder per domain; a same-day re-run overwrites it. Site
configs are read from `PSS_SEO_SITES_DIR` (default `sites/`), not from the MCP
client's working directory. `crawl.json` holds the raw crawl so rules re-run
offline.

## Privacy

The tool stores crawl data and reports for the sites it audits. These may
contain page content, titles, and URLs from real sites and should be treated as
private. Keep `reports/` and real per-site configs out of any public
repository; this repository ignores `reports/` and every `sites/*.json` except
the synthetic `example.test.json`. A PageSpeed Insights API key is read from an
environment variable and must never be committed.
