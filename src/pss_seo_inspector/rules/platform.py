"""Platform-specific rules: SHOP-01..03, MAG-01..02, WP-01."""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..models import Severity
from ..parse import jsonld_types
from ..urlutil import normalize, path_of, query_keys, strip_query
from .base import (
    AuditContext,
    Issue,
    Manual,
    Outcome,
    ProbeContext,
    ProbeSpec,
    Rule,
    canonical_is_self,
    is_noindex,
    noindex_evidence,
    status_evidence,
)
from .base import (
    linked_from_note as _from,
)

# Default Disallow lines of Shopify's generated robots.txt ("*" group), as seen on
# one live Online Store 2.0 store on 2026-10-11 — medium confidence (research.md).
SHOPIFY_DEFAULT_DISALLOWS: tuple[str, ...] = (
    "/admin",
    "/cart",
    "/orders",
    "/checkouts/",
    "/checkout",
    "/carts",
    "/account",
    "/collections/*sort_by*",
    "/collections/*+*",
    "/blogs/*+*",
    "/*?*oseid=*",
    "/*preview_theme_id*",
    "/*preview_script_id*",
    "/policies/",
    "/search",
    "/recommendations/products",
)
MAGENTO_STORE_PARAMS = {"___store", "___from_store"}
STORE_CODE_SEGMENT = re.compile(r"^(?:[a-z]{2}(?:[_-][a-z]{2})?|default)$")
MAX_STORE_CODE_PROBES = 5
MAX_ATTACHMENT_PROBES = 3
WP_IMAGE_ID = re.compile(r"\bwp-image-(\d+)\b")

# -- SHOP-01 -----------------------------------------------------------------


def _shop01_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    for kind in ("vendors", "types"):
        url = ctx.absolute(f"/collections/{kind}?q=pss{ctx.token}")
        yield ProbeSpec(key=kind, url=url)


def _shop01(ctx: AuditContext) -> Iterable[Outcome]:
    for probe in ctx.probes("SHOP-01"):
        if probe.robots_blocked:
            yield Manual(
                f"{probe.url} is blocked by robots.txt, so Google cannot see "
                "a 404 or noindex there; check by hand"
            )
            continue
        if probe.status == 200 and not is_noindex(probe):
            yield Issue(
                probe.url,
                f"Random ?q= page is indexable: "
                f"{status_evidence(probe)}; {noindex_evidence(probe)}",
            )


# -- SHOP-02 -----------------------------------------------------------------


def _shop02_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    url = ctx.absolute("/collections/all")
    yield ProbeSpec(key="collections-all", url=url)


def _shop02(ctx: AuditContext) -> Iterable[Outcome]:
    sitemap = set(ctx.sitemap_urls)
    probe = ctx.probe("SHOP-02", "collections-all")
    if probe is not None and probe.status == 200 and probe.html is not None:
        url = normalize(probe.final_url)
        canonical = probe.html.canonicals[0] if probe.html.canonicals else url
        linked = url in ctx.linked_from or url in sitemap
        if not is_noindex(probe) and normalize(canonical) == url and linked:
            yield Issue(
                url,
                "/collections/all is indexable, self-canonical and "
                f"linked or in the sitemap{_from(ctx, url)}",
            )
    for page in ctx.indexable_pages():
        if re.match(r"^/blogs/[^/]+/tagged/", path_of(page.final_url)) and (
            canonical_is_self(page)
        ):
            yield Issue(
                page.final_url,
                "Blog tag archive is indexable and "
                f"self-canonical{_from(ctx, page.final_url)}",
            )
    for target in ctx.linked_from:
        path = path_of(target)
        if path.endswith((".json", ".oembed", ".atom")):
            res = ctx.resource(target)
            if res is None or not is_noindex(res):
                yield Issue(
                    target,
                    f"Alternate format linked internally{_from(ctx, target)}",
                )
        elif "view" in query_keys(target):
            view = ctx.page(target)
            if view is None or view.html is None or is_noindex(view):
                continue
            canonical = view.html.canonicals[0] if view.html.canonicals else ""
            if normalize(canonical or target) != normalize(strip_query(target)):
                yield Issue(
                    target,
                    "?view= alternate is indexable without a canonical "
                    f"to the clean URL{_from(ctx, target)}",
                )


# -- SHOP-03 -----------------------------------------------------------------


def _shop03(ctx: AuditContext) -> Iterable[Outcome]:
    robots = ctx.crawl.robots
    if robots is None or robots.status != 200:
        yield Manual("robots.txt not available; compare with Shopify defaults by hand")
        return
    star = ctx.robots.parsed.rules_for("*")
    disallows = {r.pattern for r in star if not r.allow}
    if "/" in disallows:
        yield Issue(
            robots.url,
            "Blanket 'Disallow: /' in the '*' group blocks the whole store",
            Severity.high,
        )
    missing = [d for d in SHOPIFY_DEFAULT_DISALLOWS if d not in disallows]
    if missing:
        yield Issue(
            robots.url,
            f"{len(missing)} Shopify default Disallow lines "
            f"missing from the '*' group: {', '.join(missing)}",
        )


# -- MAG-01 ------------------------------------------------------------------


def _mag01(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.indexable_pages():
        assert page.html is not None
        classes = page.html.body_classes
        is_product = (
            "catalog-product-view" in classes
            or page.html.og.get("og:type", "").lower() == "product"
            or "Product" in jsonld_types(page.html.jsonld)
        )
        is_category = "catalog-category-view" in classes
        if not (is_product or is_category):
            continue
        kind = "Product" if is_product else "Category"
        if not page.html.canonicals:
            yield Issue(page.final_url, f"{kind} page has no canonical")
            continue
        if not is_product:
            continue
        canonical_path = path_of(page.html.canonicals[0])
        if "/catalog/product/view" in canonical_path:
            continue  # Magento's raw product route, not a category path
        segments = [s for s in canonical_path.split("/") if s]
        if segments and STORE_CODE_SEGMENT.match(segments[0]) and len(segments) > 1:
            segments = segments[1:]
        if len(segments) > 1:
            yield Issue(
                page.final_url,
                "Product canonical contains a category path: "
                f"{page.html.canonical_snippets[0]}",
            )


# -- MAG-02 ------------------------------------------------------------------


def _mag02_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    count = 0
    for page in ctx.html_pages():
        segments = [s for s in path_of(page.final_url).split("/") if s]
        if len(segments) < 2 or not STORE_CODE_SEGMENT.match(segments[0]):
            continue
        url = ctx.absolute("/" + "/".join(segments[1:]))
        yield ProbeSpec(key=page.final_url, url=url)
        count += 1
        if count >= MAX_STORE_CODE_PROBES:
            return


def _mag02(ctx: AuditContext) -> Iterable[Outcome]:
    for target in ctx.linked_from:
        keys = {k.lower() for k in query_keys(target)}
        if keys & MAGENTO_STORE_PARAMS:
            yield Issue(
                target,
                f"Store-switcher parameter in an internal link{_from(ctx, target)}",
            )
    for probe in ctx.probes("MAG-02"):
        original = ctx.page(probe.key)
        if (
            original is None
            or original.html is None
            or probe.html is None
            or probe.status != 200
            or probe.redirected
            or is_noindex(probe)
        ):
            continue
        a = normalize(original.html.canonicals[0]) if original.html.canonicals else None
        b = normalize(probe.html.canonicals[0]) if probe.html.canonicals else None
        if a is None or b is None or a != b:
            yield Issue(
                probe.url,
                f"Same page served with and without store code ({probe.key}); "
                f"canonicals {a or 'none'} vs {b or 'none'}",
            )


# -- WP-01 -------------------------------------------------------------------


def _wp01_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    ids: list[str] = []
    for page in ctx.html_pages():
        assert page.html is not None
        for img in page.html.images:
            match = WP_IMAGE_ID.search(img.css_class)
            if match and match.group(1) not in ids:
                ids.append(match.group(1))
    for attachment_id in ids[:MAX_ATTACHMENT_PROBES]:
        url = ctx.absolute(f"/?attachment_id={attachment_id}")
        yield ProbeSpec(key=attachment_id, url=url)


def _wp01(ctx: AuditContext) -> Iterable[Outcome]:
    for page in ctx.html_pages():
        assert page.html is not None
        classes = page.html.body_classes
        if "attachment" in classes or "single-attachment" in classes:
            yield Issue(
                page.final_url,
                "Attachment page returns 200 HTML (body class 'attachment')",
            )
    for probe in ctx.probes("WP-01"):
        # After the recommended 301 to the file, the final status is also 200.
        if probe.status == 200 and probe.html is not None and not probe.redirected:
            yield Issue(
                probe.url,
                f"Attachment URL serves an HTML page: {status_evidence(probe)}",
            )


RULES = [
    Rule(
        id="SHOP-01",
        category="platform",
        severity=Severity.high,
        description="Shopify: /collections/vendors?q= and /collections/types?q= with "
        "random values return 404 or noindex",
        check=_shop01,
        probes=_shop01_probes,
        applies_to=frozenset({"shopify"}),
        fix={
            "generic": "Return 404 or noindex for vendor/type listing pages with "
            "unknown values.",
            "shopify": "In theme.liquid <head> add: {% if template contains "
            "'collection' and (collection.handle == 'vendors' or collection.handle "
            '== \'types\') %}<meta name="robots" content="noindex">{% endif %}. '
            "Do NOT block these URLs in robots.txt (Google would not see the "
            "noindex).",
        },
    ),
    Rule(
        id="SHOP-02",
        category="platform",
        severity=Severity.medium,
        description="Shopify: /collections/all, /blogs/*/tagged/* and .json/?view= "
        "alternates are not indexable and linked",
        check=_shop02,
        probes=_shop02_probes,
        applies_to=frozenset({"shopify"}),
        fix={
            "generic": "Noindex or canonicalize duplicate listing and alternate "
            "views; stop linking them.",
            "shopify": "In theme.liquid add noindex for collection.handle == 'all' "
            "and when current_tags is present on blog templates; remove internal "
            "links to .json/?view= URLs; give alternate templates a canonical to "
            "the clean URL.",
        },
    ),
    Rule(
        id="SHOP-03",
        category="platform",
        severity=Severity.medium,
        description="Shopify: robots.txt keeps the platform default Disallow lines "
        "and has no blanket 'Disallow: /'",
        check=_shop03,
        applies_to=frozenset({"shopify"}),
        manual="Shopify Admin > Online Store > Themes > Edit code: review "
        "templates/robots.txt.liquid if it exists.",
        fix={
            "generic": "Restore the platform's default robots.txt rules.",
            "shopify": "In templates/robots.txt.liquid keep the loop over "
            "robots.default_groups and only add rules; delete the template to "
            "restore Shopify's defaults. (The default list is from one live store "
            "— medium confidence; verify against your store's generated file.)",
        },
    ),
    Rule(
        id="MAG-01",
        category="platform",
        severity=Severity.high,
        description="Magento 2: product and category pages have a canonical; product "
        "canonicals contain no category path",
        check=_mag01,
        applies_to=frozenset({"magento2"}),
        scope="page",
        manual="Magento Admin > Stores > Configuration > Catalog > Catalog > Search "
        "Engine Optimization: confirm both canonical settings are Yes.",
        fix={
            "generic": "Add a canonical to product and category pages pointing at "
            "the category-less product URL.",
            "magento2": "Stores > Configuration > Catalog > Catalog > Search Engine "
            "Optimization: 'Use Canonical Link Meta Tag For Categories' = Yes, "
            "'For Products' = Yes, 'Use Categories Path for Product URLs' = No.",
        },
    ),
    Rule(
        id="MAG-02",
        category="platform",
        severity=Severity.medium,
        description="Magento 2: no ___store/___from_store parameters in links; the "
        "same page is not served with and without the store code",
        check=_mag02,
        probes=_mag02_probes,
        applies_to=frozenset({"magento2"}),
        fix={
            "generic": "Link to clean URLs and serve one URL per page.",
            "magento2": "Make the store switcher link to the target URL without "
            "query parameters; 301 one store-code variant to the other (exclude "
            "admin) or set 'Add Store Code to Urls' = No (Stores > Configuration > "
            "General > Web).",
        },
    ),
    Rule(
        id="WP-01",
        category="platform",
        severity=Severity.medium,
        description="WordPress/WooCommerce: attachment pages do not return 200 HTML",
        check=_wp01,
        probes=_wp01_probes,
        applies_to=frozenset({"woocommerce"}),
        fix={
            "generic": "Redirect attachment pages to the file or parent post.",
            "woocommerce": "Set the wp_attachment_pages_enabled option to 0 (sites "
            "upgraded from before WordPress 6.4 still have it on) or enable the "
            "attachment redirect in Yoast/Rank Math.",
        },
    ),
]
