"""Platform detection from HTML, response headers and asset paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GENERIC = "generic"
PLATFORMS = ("shopify", "magento2", "woocommerce")


@dataclass(frozen=True)
class Signature:
    platform: str
    where: Literal["html", "header"]
    needle: str  # substring (html) or "name" / "name: value-substring" (header)


SIGNATURES: tuple[Signature, ...] = (
    Signature("shopify", "html", "cdn.shopify.com"),
    Signature("shopify", "html", "Shopify.theme"),
    Signature("shopify", "header", "x-shopid"),
    Signature("shopify", "header", "x-shopify-stage"),
    Signature("shopify", "header", "powered-by: shopify"),
    Signature("magento2", "html", "/static/version"),
    Signature("magento2", "html", "/static/frontend/"),
    Signature("magento2", "html", "Magento_"),
    Signature("magento2", "html", "mage/cookies"),
    Signature("magento2", "header", "x-magento-*"),
    # WooCommerce markers (case-insensitive); plain WordPress stays generic.
    Signature("woocommerce", "html", "/plugins/woocommerce/"),
    Signature("woocommerce", "html", "woocommerce-no-js"),
    Signature("woocommerce", "html", "woocommerce-page"),
    Signature("woocommerce", "html", 'class="woocommerce'),
)
CASE_INSENSITIVE = {"woocommerce"}


def _header_hit(needle: str, headers: dict[str, str]) -> bool:
    name, _, value = needle.partition(": ")
    if name.endswith("*"):
        return any(k.startswith(name[:-1]) for k in headers)
    return name in headers and (not value or value in headers[name])


def detect(html: str, headers: dict[str, str]) -> tuple[str, list[str]]:
    """Return (platform, matched signatures); ``generic`` when nothing matches."""
    lowered = {k.lower(): v.lower() for k, v in headers.items()}
    html_lower = html.lower()
    scores: dict[str, list[str]] = {}
    for sig in SIGNATURES:
        if sig.where == "html":
            if sig.platform in CASE_INSENSITIVE:
                hit = sig.needle.lower() in html_lower
            else:
                hit = sig.needle in html
        else:
            hit = _header_hit(sig.needle, lowered)
        if hit:
            scores.setdefault(sig.platform, []).append(f"{sig.where}:{sig.needle}")
    if not scores:
        return GENERIC, []
    best = max(scores, key=lambda p: len(scores[p]))
    return best, scores[best]
