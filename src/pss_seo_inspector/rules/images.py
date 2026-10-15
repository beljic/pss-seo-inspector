"""IMG-01..IMG-03: alt text, oversized images, formats and layout attributes."""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..models import Image, Page, Probe, Severity
from ..urlutil import host_of, normalize, path_of
from .base import (
    AuditContext,
    Issue,
    Manual,
    Outcome,
    ProbeContext,
    ProbeSpec,
    Rule,
    unique_pages,
)

MAX_IMAGE_PROBES = 100
OVERSIZE_FACTOR = 2
DECORATIVE_EXEMPT = re.compile(r"logo|product", re.IGNORECASE)
MODERN_EXTENSIONS = (".webp", ".avif", ".svg")
LEGACY_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif")
MODERN_TYPES = ("image/webp", "image/avif", "image/svg")
SHOPIFY_CDN_HOSTS = {"cdn.shopify.com"}
PX_VALUE = re.compile(r"^(\d+(?:\.\d+)?)px$", re.IGNORECASE)


def _probe_candidates(pages: Iterable[Page]) -> list[str]:
    """Distinct raster image URLs in crawl and DOM order."""
    seen: list[str] = []
    found: set[str] = set()
    for page in pages:
        assert page.html is not None
        for image in page.html.images:
            url = image.src
            if not url.startswith(("http://", "https://")):
                continue
            if path_of(url).lower().endswith(".svg"):
                continue
            key = normalize(url)
            if key not in found:
                found.add(key)
                seen.append(url)
    return seen


def _image_probes(ctx: ProbeContext) -> Iterable[ProbeSpec]:
    for url in _probe_candidates(unique_pages(ctx.html_pages()))[:MAX_IMAGE_PROBES]:
        yield ProbeSpec(key=normalize(url), url=url, kind="image")


def _probe_for(ctx: AuditContext, url: str) -> Probe | None:
    return ctx.probe("IMG-02", normalize(url))


def _pages(ctx: AuditContext) -> list[Page]:
    return unique_pages(ctx.canonical_pages())


def _old_crawl(page: Page, field: str) -> bool:
    """True when the page was parsed before ``field`` existed (old crawl.json)."""
    assert page.html is not None
    return any(field not in image.model_fields_set for image in page.html.images)


def _recrawl_manual(count: int, check: str) -> Manual:
    return Manual(
        f"{count} page(s) from an older crawl.json lack image data for the {check} "
        "check; re-crawl"
    )


# -- IMG-01 ------------------------------------------------------------------


def _img01(ctx: AuditContext) -> Iterable[Outcome]:
    for page in _pages(ctx):
        assert page.html is not None
        for image in page.html.images:
            if image.alt is None:
                yield Issue(page.final_url, f"<img> without alt: {image.snippet}")
            elif not image.alt.strip():
                if image.link_text == "":
                    reason = "inside a link with no other text"
                elif DECORATIVE_EXEMPT.search(image.css_class):
                    reason = "logo/product image"
                else:
                    continue  # empty alt = decorative, which is valid
                yield Issue(page.final_url, f"Empty alt ({reason}): {image.snippet}")


# -- IMG-02 ------------------------------------------------------------------


def _sizes_px(sizes: str) -> float | None:
    """Largest px length in a sizes attribute (media conditions ignored)."""
    best: float | None = None
    for entry in sizes.split(","):
        value = entry.strip()
        if value.endswith(")") and not value.startswith("("):
            continue  # calc(...) and similar
        if ")" in value:
            value = value.rsplit(")", 1)[1].strip()
        match = PX_VALUE.match(value)
        if match:
            px = float(match.group(1))
            best = px if best is None else max(best, px)
    return best


def _srcset_widths(srcset: str) -> list[int]:
    widths: list[int] = []
    for candidate in srcset.split(","):
        parts = candidate.strip().split()
        if len(parts) >= 2 and parts[-1].lower().endswith("w"):
            number = parts[-1][:-1]
            if number.isdigit():
                widths.append(int(number))
    return widths


def _declared(image: Image) -> float | None:
    """The slot width: px in sizes, else the width attribute (often intrinsic)."""
    sizes = _sizes_px(image.sizes)
    if sizes is not None:
        return sizes
    return float(image.width) if image.width and image.width.isdigit() else None


def _img02(ctx: AuditContext) -> Iterable[Outcome]:
    old_pages = 0
    for page in _pages(ctx):
        assert page.html is not None
        old = _old_crawl(page, "sizes")
        old_pages += old
        for image in page.html.images:
            widths = _srcset_widths(image.srcset)
            if widths and old:
                continue
            if widths and not image.sizes.strip():
                yield Issue(
                    page.final_url,
                    "srcset with w-descriptors but no sizes (browser assumes 100vw): "
                    f"{image.snippet}",
                )
                continue
            declared = _declared(image)
            if declared is None:
                continue
            limit = OVERSIZE_FACTOR * declared
            if widths:
                # The browser picks from srcset; only certain if even the smallest
                # candidate is too big.
                smallest = min(widths)
                if smallest > limit:
                    yield Issue(
                        page.final_url,
                        f"Smallest srcset candidate is {smallest}w for a "
                        f"{declared:g}px slot: {image.snippet}",
                    )
                continue
            probe = _probe_for(ctx, image.src)
            if probe is None or probe.image_width is None:
                continue
            if probe.image_width > limit:
                yield Issue(
                    page.final_url,
                    f"Image is {probe.image_width}px wide for a {declared:g}px slot "
                    f"(>{OVERSIZE_FACTOR}x): {image.snippet}",
                )
    if old_pages:
        yield _recrawl_manual(old_pages, "srcset/sizes")
    candidates = _probe_candidates(_pages(ctx))
    unprobed = [u for u in candidates if _probe_for(ctx, u) is None]
    if unprobed and not ctx.probes("IMG-02"):
        yield Manual(
            f"Image probes were not run for this crawl ({len(unprobed)} image(s)); "
            "re-crawl to check intrinsic sizes"
        )
    elif unprobed:
        yield Manual(
            f"{len(unprobed)} image(s) not probed (limit {MAX_IMAGE_PROBES}); check "
            "their intrinsic size by hand"
        )


# -- IMG-03 ------------------------------------------------------------------


def _shopify_cdn(url: str) -> bool:
    path = path_of(url)
    return host_of(url) in SHOPIFY_CDN_HOSTS or path.startswith(
        ("/cdn/shop/", "/s/files/")
    )


def _legacy_format(ctx: AuditContext, url: str) -> str | None:
    """The legacy format name, or None when modern / negotiated / unknown."""
    if not url.startswith(("http://", "https://")) or _shopify_cdn(url):
        return None
    path = path_of(url).lower()
    if path.endswith(MODERN_EXTENSIONS):
        return None
    probe = _probe_for(ctx, url)
    if probe is not None and probe.status in (200, 206):
        if "accept" in probe.headers.get("vary", "").lower():
            return None
        content_type = probe.content_type.split(";")[0].strip().lower()
        if content_type.startswith(MODERN_TYPES):
            return None
        if content_type.startswith("image/"):
            return content_type
    if path.endswith(LEGACY_EXTENSIONS):
        return path.rsplit(".", 1)[-1]
    return None


def _img03(ctx: AuditContext) -> Iterable[Outcome]:
    formats_seen: set[str] = set()
    old_pages = 0
    for page in _pages(ctx):
        assert page.html is not None
        images = page.html.images
        for image in images:
            key = normalize(image.src) if image.src else ""
            if image.picture_modern:
                continue
            if key and key not in formats_seen:
                formats_seen.add(key)
                legacy = _legacy_format(ctx, image.src)
                if legacy:
                    yield Issue(
                        page.final_url,
                        f"Served as {legacy}, no WebP/AVIF or format negotiation: "
                        f"{image.snippet}",
                    )
        no_dims = [i for i in images if not (i.width and i.height)]
        if no_dims:
            yield Issue(
                page.final_url,
                f"{len(no_dims)} <img> without width/height (layout shift), e.g. "
                f"{no_dims[0].snippet}",
            )
        if _old_crawl(page, "landmark"):
            old_pages += 1
            continue
        first = next((i for i in images if not i.landmark), None)
        if first is not None and (first.loading or "").lower() == "lazy":
            yield Issue(
                page.final_url,
                f"First content image (LCP candidate) is loading=lazy: {first.snippet}",
                severity=Severity.medium,
            )
    if old_pages:
        yield _recrawl_manual(old_pages, "lazy LCP image")


RULES = [
    Rule(
        id="IMG-01",
        category="images",
        severity=Severity.medium,
        description="<img> has alt text (empty alt only for decorative images, not "
        "for linked, logo or product images)",
        check=_img01,
        scope="page",
        fix={
            "generic": 'Add descriptive alt text; use alt="" only for purely '
            "decorative images that are not links.",
            "shopify": "Set alt text in Admin > Products/Files (image > Add alt "
            "text); in Liquid pass alt: image.alt | default: product.title to "
            "image_tag.",
            "magento2": "Set the image Alt Text in the product's Images and Videos "
            "panel; check templates print it.",
            "woocommerce": "Fill Alternative Text in the Media Library.",
        },
    ),
    Rule(
        id="IMG-02",
        category="images",
        severity=Severity.medium,
        description="Images not larger than 2x their declared width / sizes slot; "
        "srcset with w-descriptors has sizes",
        check=_img02,
        probes=_image_probes,
        scope="page",
        fix={
            "generic": "Serve images close to their rendered size: add srcset with "
            "w-descriptors and an accurate sizes attribute.",
            "shopify": "Use image_url with a width plus image_tag widths: and an "
            "accurate sizes: for the slot (e.g. product thumbnails).",
            "magento2": "Use resized image roles (view.xml) for thumbnails, not the "
            "original upload.",
            "woocommerce": "Register a fitting image size and regenerate "
            "thumbnails; check the theme's sizes attribute.",
        },
    ),
    Rule(
        id="IMG-03",
        category="images",
        severity=Severity.low,
        description="Images in WebP/AVIF (or CDN-negotiated), with width/height; "
        "first content image not lazy-loaded",
        check=_img03,
        scope="page",
        fix={
            "generic": "Serve WebP/AVIF (or let the CDN negotiate), set width and "
            "height on every <img>, and never lazy-load the LCP image.",
            "shopify": "Use image_url/image_tag (Shopify CDN negotiates WebP/AVIF; "
            "image_tag adds width/height); pass loading: 'eager' and fetchpriority: "
            "'high' for the first section's image.",
            "magento2": "Enable WebP via an image optimisation module or CDN; set "
            "width/height in templates.",
            "woocommerce": "Enable WebP in an image plugin or CDN; WordPress adds "
            "width/height when the theme uses wp_get_attachment_image.",
        },
    ),
]
