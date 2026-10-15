"""URL helpers used everywhere URLs are compared."""

from __future__ import annotations

from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from .models import EVIDENCE_SNIPPET_MAX

DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize(url: str) -> str:
    """Lower-case scheme/host, drop default port and fragment, empty path → "/"."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:  # e.g. non-numeric port: keep it verbatim, never crash
        return url.strip().split("#", 1)[0]
    scheme = parts.scheme.lower()
    netloc = host
    if port is not None and DEFAULT_PORTS.get(scheme) != port:
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def absolutize(base: str, href: str) -> str | None:
    """Resolve ``href`` against ``base``; None for non-HTTP schemes."""
    href = href.strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    try:
        joined = urljoin(base, href)
        parts = urlsplit(joined)
        _ = parts.port  # raises ValueError for an invalid port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return normalize(joined)


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def origin_of(url: str) -> str:
    parts = urlsplit(normalize(url))
    return f"{parts.scheme}://{parts.netloc}"


def path_of(url: str) -> str:
    return urlsplit(url).path or "/"


def path_and_query(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path


def query_keys(url: str) -> list[str]:
    return [k for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)]


def strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))


def domain_of(url: str) -> str:
    host = host_of(url)
    return host[4:] if host.startswith("www.") else host


def snip(text: str, limit: int = EVIDENCE_SNIPPET_MAX) -> str:
    """Collapse whitespace and cap at ``limit`` characters."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
