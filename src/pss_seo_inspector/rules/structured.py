"""SD-00..SD-08: JSON-LD structured data."""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any

from ..models import Page, Severity
from ..urlutil import path_of, snip
from .base import AuditContext, Issue, Manual, Outcome, Rule, unique_pages
from .jsonld import Node, PageData, as_list, node_snippet, page_data, present, types_of

PRODUCT_REQUIRED = ("name", "image")
OFFER_REQUIRED = ("price", "priceCurrency", "availability", "url")
AGGREGATE_OFFER_REQUIRED = ("price", "priceCurrency")  # price = lowPrice
ORG_TYPES = {"Organization", "Corporation", "NGO", "Store", "LocalBusiness"}
ORG_SUFFIXES = ("Store", "Business")
PRODUCT_BODY_CLASSES = {"catalog-product-view", "single-product"}
CATEGORY_BODY_CLASSES = {"catalog-category-view", "tax-product_cat"}
SHOPIFY_COLLECTION = re.compile(r"^/collections/[^/]+/?$")
RETIRED_TYPES = {
    "FAQPage",
    "ClaimReview",
    "Course",
    "EstimatedSalary",
    "Occupation",
    "LearningVideo",
    "SpecialAnnouncement",
    "Vehicle",
    "Car",
    "Dataset",
}
BOOK_ACTIONS = {"ReadAction", "BorrowAction"}
FAQ_HEADINGS_MIN = 3
FAQ_MATCH_CHARS = 80


def _pages(ctx: AuditContext) -> list[tuple[Page, PageData]]:
    return [(p, page_data(p)) for p in unique_pages(ctx.canonical_pages())]


def _home(ctx: AuditContext) -> Page | None:
    for url in (ctx.origin + "/", ctx.crawl.start_url):
        found = ctx.page(url)
        if found is not None and found.html is not None and found.status == 200:
            return found
    return None


def _is_org(types: frozenset[str]) -> bool:
    return any(t in ORG_TYPES or t.endswith(ORG_SUFFIXES) for t in types)


def _orgs(data: PageData) -> list[Node]:
    return [n.node for n in data.nodes if _is_org(n.types)]


def _product_sets(data: PageData) -> tuple[list[tuple[Node, Node | None]], list[Node]]:
    """(products to check with their ProductGroup, product holders).

    A top-level ProductGroup is one holder; its hasVariant Products are checked
    (with the group's fields as fallback), and so is the group when it has offers.
    """
    checked: list[tuple[Node, Node | None]] = []
    holders: list[Node] = []
    variant_ids: set[int] = set()
    for group in data.of_type("ProductGroup", top_only=True):
        variants = [
            v
            for v in (data.resolve(x) for x in as_list(group.get("hasVariant")))
            if isinstance(v, dict)
        ]
        variant_ids |= {id(v) for v in variants}
        holders.append(group)
        if present(group.get("offers")) or not variants:
            checked.append((group, None))
        checked.extend((v, group) for v in variants)
    for product in data.of_type("Product", top_only=True):
        if id(product) not in variant_ids and "ProductGroup" not in types_of(product):
            checked.append((product, None))
            holders.append(product)
    return checked, holders


def _products(data: PageData) -> list[Node]:
    return [node for node, _ in _product_sets(data)[0]]


def _is_product_page(page: Page, data: PageData) -> bool:
    assert page.html is not None
    return (
        bool(_products(data))
        or page.html.og.get("og:type", "").lower() == "product"
        or bool(PRODUCT_BODY_CLASSES & set(page.html.body_classes))
    )


def _is_category_page(ctx: AuditContext, page: Page, data: PageData) -> bool:
    assert page.html is not None
    return (
        bool(data.of_type("CollectionPage"))
        or bool(CATEGORY_BODY_CLASSES & set(page.html.body_classes))
        or (
            ctx.platform == "shopify"
            and SHOPIFY_COLLECTION.match(path_of(page.final_url)) is not None
        )
    )


def _label(node: Node) -> str:
    name = node.get("name")
    return f"'{name}'" if isinstance(name, str) and name else "(no name)"


# -- SD-00 -------------------------------------------------------------------


def _sd00(ctx: AuditContext) -> Iterable[Outcome]:
    for page in unique_pages(ctx.html_pages()):
        for block in page_data(page).errors:
            yield Issue(
                page.final_url,
                f"JSON-LD block #{block.index + 1} is not valid JSON ({block.error}): "
                f"{snip(block.raw, 200)}",
            )


# -- SD-01 -------------------------------------------------------------------


def _offers(product: Node, data: PageData) -> list[Node]:
    return [
        o
        for o in (data.resolve(v) for v in as_list(product.get("offers")))
        if isinstance(o, dict)
    ]


def _offer_value(offer: Node, key: str) -> Any:
    value = offer.get(key)
    if present(value) or key not in ("price", "priceCurrency"):
        return value
    if key == "price" and "AggregateOffer" in types_of(offer):
        if present(offer.get("lowPrice")):
            return offer.get("lowPrice")
    for spec in as_list(offer.get("priceSpecification")):
        if isinstance(spec, dict) and present(spec.get(key)):
            return spec.get(key)
    return None


def _required_offer_keys(offer: Node) -> tuple[str, ...]:
    if "AggregateOffer" in types_of(offer):
        return AGGREGATE_OFFER_REQUIRED
    return OFFER_REQUIRED


def _get(node: Node, parent: Node | None, key: str) -> Any:
    value = node.get(key)
    if not present(value) and parent is not None:
        return parent.get(key)
    return value


def _missing_required(
    product: Node, offers: list[Node], parent: Node | None = None
) -> list[str]:
    missing = [k for k in PRODUCT_REQUIRED if not present(_get(product, parent, k))]
    if not offers:
        return missing + [f"offers.{k}" for k in OFFER_REQUIRED]
    for key in OFFER_REQUIRED:
        if any(
            key in _required_offer_keys(o) and not present(_offer_value(o, key))
            for o in offers
        ):
            missing.append(f"offers.{key}")
    return missing


def _missing_recommended(
    product: Node, offers: list[Node], parent: Node | None = None
) -> list[str]:
    holders = [product, *offers, *([parent] if parent is not None else [])]
    missing = [
        k for k in ("description", "brand") if not present(_get(product, parent, k))
    ]
    if not any(present(h.get("sku")) for h in holders):
        missing.append("sku")
    if not any(
        present(v) for h in holders for k, v in h.items() if k.startswith("gtin")
    ) and not any(present(h.get("mpn")) for h in holders):
        missing.append("gtin/mpn")
    if not any(
        present(h.get("aggregateRating")) or present(h.get("review")) for h in holders
    ):
        missing.append("aggregateRating/review")
    return missing


def _first(offers: list[Node], key: str) -> str:
    for offer in offers:
        value = _offer_value(offer, key)
        if present(value):
            return str(value)
    return ""


def _holder_offers(holder: Node, data: PageData) -> list[Node]:
    offers = _offers(holder, data)
    if offers:
        return offers
    for variant in as_list(holder.get("hasVariant")):
        variant = data.resolve(variant)
        if isinstance(variant, dict) and _offers(variant, data):
            return _offers(variant, data)
    return []


def _price_key(value: str) -> str:
    try:
        return str(Decimal(str(value)).normalize())
    except InvalidOperation:
        return value


def _conflicts(products: list[Node], data: PageData) -> list[str]:
    offers = [_holder_offers(p, data) for p in products]
    fields = {
        "name": [str(p.get("name") or "") for p in products],
        "price": [_first(o, "price") for o in offers],
        "priceCurrency": [_first(o, "priceCurrency") for o in offers],
    }
    out: list[str] = []
    for key, values in fields.items():
        by_key: dict[str, str] = {}
        for value in values:
            if value:
                by_key.setdefault(_price_key(value) if key == "price" else value, value)
        distinct = list(by_key.values())
        if len(distinct) > 1:
            out.append(f"{key} differs: " + " vs ".join(distinct))
    return out


def _sd01(ctx: AuditContext) -> Iterable[Outcome]:
    for page, data in _pages(ctx):
        if not _is_product_page(page, data):
            continue
        checked, holders = _product_sets(data)
        if not checked:
            yield Issue(page.final_url, "Product page without Product JSON-LD")
            continue
        recommended: list[str] = []
        for product, parent in checked:
            offers = _offers(product, data)
            missing = _missing_required(product, offers, parent)
            if missing:
                yield Issue(
                    page.final_url,
                    f"Product {_label(product)} missing required "
                    f"{', '.join(missing)}: {node_snippet(product)}",
                )
            for key in _missing_recommended(product, offers, parent):
                if key not in recommended:
                    recommended.append(key)
        if recommended:
            yield Issue(
                page.final_url,
                f"Product {_label(checked[0][0])} missing recommended "
                f"{', '.join(recommended)}",
                severity=Severity.low,
            )
        if len(holders) > 1:
            conflicts = _conflicts(holders, data)
            detail = f"; {'; '.join(conflicts)}" if conflicts else ""
            yield Issue(
                page.final_url,
                f"{len(holders)} Product nodes on one page (theme + app?){detail}",
                severity=Severity.medium,
            )


# -- SD-02 -------------------------------------------------------------------


def _org_missing(org: Node) -> list[str]:
    return [k for k in ("name", "url", "logo", "sameAs") if not present(org.get(k))]


def _sd02(ctx: AuditContext) -> Iterable[Outcome]:
    home = _home(ctx)
    if home is None:
        yield Manual("Home page not crawled; check Organization markup by hand")
        return
    orgs = _orgs(page_data(home))
    if not orgs:
        yield Issue(
            home.final_url,
            "No Organization/OnlineStore JSON-LD on the home page",
        )
        return
    best = min(orgs, key=lambda o: len(_org_missing(o)))
    missing = _org_missing(best)
    if missing:
        yield Issue(
            home.final_url,
            f"Organization {_label(best)} missing {', '.join(missing)}: "
            f"{node_snippet(best)}",
        )


# -- SD-03 -------------------------------------------------------------------


def _sd03(ctx: AuditContext) -> Iterable[Outcome]:
    for page, data in _pages(ctx):
        if data.of_type("BreadcrumbList"):
            continue
        if _is_product_page(page, data):
            kind = "Product"
        elif _is_category_page(ctx, page, data):
            kind = "Category"
        else:
            continue
        yield Issue(page.final_url, f"{kind} page without BreadcrumbList JSON-LD")


# -- SD-04 -------------------------------------------------------------------


def _norm(text: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    return " ".join(text.lower().split())


def _visible(snippet: str, page_text: str) -> bool:
    needle = _norm(snippet)[:FAQ_MATCH_CHARS]
    return not needle or needle in page_text


def _answer_text(question: Node) -> str:
    for answer in as_list(question.get("acceptedAnswer")):
        if isinstance(answer, dict) and isinstance(answer.get("text"), str):
            return str(answer["text"])
    return ""


def _sd04(ctx: AuditContext) -> Iterable[Outcome]:
    for page, data in _pages(ctx):
        assert page.html is not None
        if "text" not in page.html.model_fields_set:
            yield Manual(f"{page.final_url}: crawl has no page text; re-crawl")
            continue
        faqs = data.of_type("FAQPage")
        if not faqs:
            questions = page.html.question_headings
            if len(questions) >= FAQ_HEADINGS_MIN:
                shown = "; ".join(questions[:3])
                yield Issue(
                    page.final_url,
                    f"{len(questions)} question headings without FAQPage JSON-LD: "
                    f"{shown}",
                )
            continue
        page_text = _norm(page.html.text)
        for faq in faqs:
            for question in as_list(faq.get("mainEntity")):
                if not isinstance(question, dict):
                    continue
                name = str(question.get("name") or "")
                answer = _answer_text(question)
                hidden = [
                    part
                    for part, text in (("question", name), ("answer", answer))
                    if not _visible(text, page_text)
                ]
                if hidden:
                    yield Issue(
                        page.final_url,
                        f"FAQPage {' and '.join(hidden)} not visible on the page: "
                        f"'{snip(name, 120)}'",
                    )


# -- SD-05 -------------------------------------------------------------------


def _targets(action: Node) -> list[str]:
    out: list[str] = []
    for target in as_list(action.get("target")):
        if isinstance(target, str):
            out.append(target)
        elif isinstance(target, dict) and isinstance(target.get("urlTemplate"), str):
            out.append(str(target["urlTemplate"]))
    return out


def _placeholder(action: Node) -> str:
    for value in as_list(action.get("query-input")):
        text = value if isinstance(value, str) else ""
        if isinstance(value, dict):
            text = str(value.get("valueName") or "")
            text = f"name={text}" if text else ""
        match = re.search(r"name=(\S+)", text)
        if match:
            return match.group(1)
    return "search_term_string"


def _sd05(ctx: AuditContext) -> Iterable[Outcome]:
    first: str | None = None
    count = 0
    for page, data in _pages(ctx):
        actions = data.of_type("SearchAction")
        if not actions:
            continue
        count += 1
        if first is None:
            first = page.final_url
            for action in actions:
                name = _placeholder(action)
                bad = [t for t in _targets(action) if f"{{{name}}}" not in t]
                if bad or not _targets(action):
                    yield Issue(
                        page.final_url,
                        f"SearchAction target lacks {{{name}}}: {node_snippet(action)}",
                    )
    if first is not None:
        yield Issue(
            first,
            f"WebSite SearchAction present on {count} page(s); Google retired the "
            "sitelinks search box (Nov 2024), so it has no search effect",
        )


# -- SD-06 -------------------------------------------------------------------


def _retired(data: PageData) -> set[str]:
    found: set[str] = set()
    for flat in data.nodes:
        found |= flat.types & RETIRED_TYPES
        if "Book" in flat.types:
            for action in as_list(flat.node.get("potentialAction")):
                if isinstance(action, dict) and types_of(action) & BOOK_ACTIONS:
                    found.add("Book actions")
    return found


def _sd06(ctx: AuditContext) -> Iterable[Outcome]:
    seen: dict[str, list[str]] = {}
    for page, data in _pages(ctx):
        for type_name in sorted(_retired(data)):
            seen.setdefault(type_name, []).append(page.final_url)
    for type_name, urls in seen.items():
        yield Issue(
            urls[0],
            f"{type_name} markup on {len(urls)} page(s); Google no longer shows this "
            "rich result. Keep it only if other consumers use it",
        )


# -- SD-07 -------------------------------------------------------------------


def _policy_state(value: Any, data: PageData) -> str:
    """'ok', 'no country' or 'missing' for a hasMerchantReturnPolicy value."""
    policies = [data.resolve(v) for v in as_list(value)]
    if not policies:
        return "missing"
    for policy in policies:
        if isinstance(policy, dict) and present(policy.get("returnPolicyCountry")):
            return "ok"
    return "no country"


def _sd07(ctx: AuditContext) -> Iterable[Outcome]:
    pages = _pages(ctx)
    product_pages = [(p, d) for p, d in pages if _products(d)]
    if not product_pages:
        return
    org_return = "missing"
    org_shipping = False
    for page in unique_pages(ctx.html_pages()):
        data = page_data(page)
        for org in _orgs(data):
            state = _policy_state(org.get("hasMerchantReturnPolicy"), data)
            if state == "ok" or (state == "no country" and org_return == "missing"):
                org_return = state
            org_shipping = org_shipping or present(org.get("hasShippingService"))
    for page, data in product_pages:
        offers = [o for p in _products(data) for o in _offers(p, data)]
        problems: list[str] = []
        if org_return != "ok":
            states = {
                _policy_state(o.get("hasMerchantReturnPolicy"), data) for o in offers
            }
            if "ok" not in states:
                if "no country" in states or org_return == "no country":
                    problems.append(
                        "hasMerchantReturnPolicy without returnPolicyCountry"
                    )
                else:
                    problems.append(
                        "no hasMerchantReturnPolicy on offers or the Organization"
                    )
        if not org_shipping and not any(
            present(o.get("shippingDetails")) for o in offers
        ):
            problems.append(
                "no shippingDetails on offers and no Organization hasShippingService"
            )
        if problems:
            yield Issue(page.final_url, "; ".join(problems))


# -- SD-08 -------------------------------------------------------------------


def _sd08(ctx: AuditContext) -> Iterable[Outcome]:
    home = _home(ctx)
    if home is None:
        yield Manual("Home page not crawled; check WebSite markup by hand")
        return
    sites: dict[str, Node] = {}
    for index, node in enumerate(page_data(home).of_type("WebSite", top_only=True)):
        node_id = node.get("@id")
        key = f"@id:{node_id}" if isinstance(node_id, str) else f"#{index}"
        if key in sites and len(node) <= len(sites[key]):
            continue
        sites[key] = node
    if not sites:
        yield Issue(home.final_url, "No WebSite JSON-LD on the home page (site name)")
        return
    if len(sites) > 1:
        names = ", ".join(_label(n) for n in sites.values())
        yield Issue(
            home.final_url, f"{len(sites)} WebSite nodes on the home page: {names}"
        )
    site = next(iter(sites.values()))
    missing = [k for k in ("name", "url") if not present(site.get(k))]
    if missing:
        yield Issue(
            home.final_url,
            f"WebSite missing {', '.join(missing)}: {node_snippet(site)}",
        )
    elif not present(site.get("alternateName")):
        yield Issue(
            home.final_url,
            f"WebSite {_label(site)} has no alternateName (recommended for site names)",
            severity=Severity.info,
        )


RULES = [
    Rule(
        id="SD-00",
        category="structured-data",
        severity=Severity.high,
        description="Every JSON-LD block is valid JSON",
        check=_sd00,
        scope="page",
        fix={
            "generic": "Fix the JSON syntax (trailing commas, unescaped quotes, "
            "unescaped Liquid/PHP output); validate with the Rich Results Test.",
            "shopify": "Escape values with the | json filter in the theme/app snippet "
            "that prints the block.",
            "magento2": "Check the module/template printing the block; use "
            "$escaper or json_encode for values.",
            "woocommerce": "Check the SEO plugin or theme hook printing the block.",
        },
    ),
    Rule(
        id="SD-01",
        category="structured-data",
        severity=Severity.high,
        description="Product JSON-LD: name, image, offers.price/priceCurrency/"
        "availability/url; recommended fields; one Product per page without "
        "conflicting values",
        check=_sd01,
        scope="page",
        fix={
            "generic": "Output one Product node with offers (price, priceCurrency, "
            "availability, url) plus sku, brand, gtin/mpn, description and reviews.",
            "shopify": "Use one source of Product JSON-LD: the theme's "
            "structured_data filter or a single app (e.g. reviews app); disable "
            "the duplicate and add missing fields via metafields.",
            "magento2": "Fix the product schema in the theme or SEO extension; "
            "avoid both printing Product.",
            "woocommerce": "Let WooCommerce or the SEO plugin output Product, not "
            "both; fill SKU/GTIN fields.",
        },
    ),
    Rule(
        id="SD-02",
        category="structured-data",
        severity=Severity.medium,
        description="Home page has Organization/OnlineStore JSON-LD with name, url, "
        "logo, sameAs",
        check=_sd02,
        scope="page",
        fix={
            "generic": "Add an Organization (or OnlineStore) node on the home page "
            "with name, url, logo and sameAs social profiles.",
            "shopify": "Set the logo and social links in Theme settings; check the "
            "theme's Organization JSON-LD in theme.liquid or the header section.",
            "magento2": "Add Organization markup via the theme head or an SEO "
            "extension.",
            "woocommerce": "Set Organization name, logo and social profiles in the "
            "SEO plugin settings.",
        },
    ),
    Rule(
        id="SD-03",
        category="structured-data",
        severity=Severity.low,
        description="Product and category pages have BreadcrumbList JSON-LD",
        check=_sd03,
        scope="page",
        fix={
            "generic": "Add BreadcrumbList markup that matches the visible breadcrumb.",
            "shopify": "Add a breadcrumbs snippet with BreadcrumbList JSON-LD to "
            "main-product and main-collection sections.",
            "magento2": "Enable breadcrumbs and BreadcrumbList via an SEO extension.",
            "woocommerce": "Enable breadcrumbs in the SEO plugin.",
        },
    ),
    Rule(
        id="SD-04",
        category="structured-data",
        severity=Severity.info,
        description="FAQPage Q&A is visible on the page; FAQ-like headings without "
        "FAQPage (FAQ rich results retired 2026-05-07)",
        check=_sd04,
        scope="page",
        fix={
            "generic": "Only mark up questions and answers that are visible on the "
            "page. FAQ rich results are no longer shown, so new FAQPage markup is "
            "optional.",
            "shopify": "Render the FAQPage JSON-LD from the same blocks/metaobjects "
            "as the visible FAQ section.",
        },
    ),
    Rule(
        id="SD-05",
        category="structured-data",
        severity=Severity.info,
        description="WebSite SearchAction present (sitelinks search box retired); "
        "target has the query placeholder",
        check=_sd05,
        scope="page",
        fix={
            "generic": "SearchAction no longer affects Google Search; keep it only "
            "with a valid target containing {search_term_string}, or remove it.",
            "shopify": "Check the WebSite JSON-LD in theme.liquid; target should be "
            "{{ shop.url }}/search?q={search_term_string}.",
        },
    ),
    Rule(
        id="SD-06",
        category="structured-data",
        severity=Severity.info,
        description="Retired rich-result markup present (FAQPage, ClaimReview, "
        "Course, EstimatedSalary, SpecialAnnouncement, Vehicle, Book actions, "
        "Dataset, ...)",
        check=_sd06,
        scope="page",
        fix={
            "generic": "These types no longer produce Google rich results; remove "
            "them unless another consumer needs them.",
        },
    ),
    Rule(
        id="SD-07",
        category="structured-data",
        severity=Severity.medium,
        description="Return policy (hasMerchantReturnPolicy + returnPolicyCountry) "
        "and shipping (shippingDetails or Organization hasShippingService) declared",
        check=_sd07,
        scope="page",
        fix={
            "generic": "Add hasMerchantReturnPolicy (with returnPolicyCountry) and "
            "hasShippingService to the Organization, or MerchantReturnPolicy and "
            "shippingDetails to each Offer; or configure them in Merchant Center.",
            "shopify": "Add the policies to the Organization JSON-LD in "
            "theme.liquid, or set return/shipping policies in Google Merchant "
            "Center via the Google & YouTube app.",
            "magento2": "Add policy markup via the theme or an SEO extension, or "
            "configure it in Merchant Center.",
            "woocommerce": "Use an SEO/feed plugin that outputs return and shipping "
            "policy markup.",
        },
    ),
    Rule(
        id="SD-08",
        category="structured-data",
        severity=Severity.medium,
        description="Home page has exactly one WebSite node with name and url "
        "(alternateName recommended)",
        check=_sd08,
        scope="page",
        fix={
            "generic": "Output one WebSite node on the home page with name, url and "
            "alternateName (the site name Google should show).",
            "shopify": "Check theme.liquid and apps for duplicate WebSite JSON-LD; "
            "keep one with name = shop name.",
            "magento2": "Add WebSite markup once via the theme head.",
            "woocommerce": "Set the site name in the SEO plugin; remove duplicates.",
        },
    ),
]
