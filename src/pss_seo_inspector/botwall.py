"""Recognize bot-protection challenge responses so they are never audited.

Detection only: the tool reports the block and stops; it never tries to pass,
solve or avoid a challenge.
"""

from __future__ import annotations

from collections.abc import Mapping

CLOUDFLARE = "Cloudflare challenge"
CHALLENGE_STATUSES = frozenset({403, 503})
_SCAN_BYTES = 64 * 1024
_CLOUDFLARE_MARKERS = (
    b"<title>just a moment...</title>",
    b"/cdn-cgi/challenge-platform/",
    b"_cf_chl_opt",
    b"cf-chl",
)


def challenge_reason(
    status: int | None, headers: Mapping[str, str], body: bytes
) -> str | None:
    """Label of the bot protection that answered, or None for a normal response.

    ``headers`` keys are lower-case (as stored on ``Resource``).
    """
    if headers.get("cf-mitigated", "").strip().lower() == "challenge":
        return CLOUDFLARE
    if status in CHALLENGE_STATUSES and looks_like_challenge(body):
        return CLOUDFLARE
    return None


def looks_like_challenge(body: bytes) -> bool:
    """Challenge-page markup, for a saved page whose HTTP status is unknown."""
    head = body[:_SCAN_BYTES].lower()
    return any(marker in head for marker in _CLOUDFLARE_MARKERS)
