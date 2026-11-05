"""Build a Crawl from pages the user saved in their own browser (no network).

For sites behind bot protection: the person opens the pages themselves and
saves them (.html "Save Page As, HTML only", or a DevTools .har export); the
rules then run over that offline. Nothing here sends a request.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .botwall import CLOUDFLARE, challenge_reason, looks_like_challenge
from .crawler import ensure_url
from .fetcher import decode, is_html
from .models import Crawl, Hop, Page
from .parse import parse_html
from .platform import detect
from .urlutil import domain_of, host_of, normalize, origin_of

MAX_FILES = 200
MAX_FILE_BYTES = 20 * 1024 * 1024
HTML_SUFFIXES = frozenset({".html", ".htm"})
IMPORT_USER_AGENT = "(imported from browser files; no requests sent)"


class ImportPagesError(ValueError):
    """The paths or the mapping cannot be imported."""


@dataclass
class _Doc:
    url: str
    status: int
    headers: dict[str, str]
    content_type: str
    body: bytes
    saved_file: bool = False  # a saved .html: no HTTP status or headers known


def import_pages(
    site_url: str,
    paths: list[str],
    *,
    urls: Mapping[str, str] | None = None,
    today: date | None = None,
) -> tuple[Crawl, list[str]]:
    """Crawl of the importable pages, plus one note per skipped file or entry."""
    start = ensure_url(site_url)
    host = host_of(start)
    files = _validate(paths, urls or {})
    crawl = Crawl(
        crawl_id=f"{domain_of(start)}/{(today or date.today()).isoformat()}-import",
        domain=domain_of(start),
        start_url=start,
        origin=origin_of(start),
        host=host,
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        user_agent=IMPORT_USER_AGENT,
        rate=0.0,
        max_pages=0,
        source="import",
    )
    skipped: list[str] = []
    seen: set[str] = set()
    detect_from: _Doc | None = None  # home page, else the first imported page
    for given, path in files:
        data = path.read_bytes()
        if path.suffix.lower() in HTML_SUFFIXES:
            docs = _html_doc(given, data, (urls or {}).get(given), start, skipped)
        else:
            docs = _har_docs(given, data, skipped)
        for doc in docs:
            label = f"{given}: {doc.url}"
            if host_of(doc.url) != host:
                skipped.append(f"{label}: not on {host}")
                continue
            reason = challenge_reason(doc.status, doc.headers, doc.body)
            if reason is None and doc.saved_file and looks_like_challenge(doc.body):
                reason = CLOUDFLARE
            if reason is not None:
                crawl.blocked_count += 1
                crawl.bot_protection = crawl.bot_protection or reason
                skipped.append(f"{label}: {reason} page")
                continue
            if doc.status != 200:
                continue  # redirects and errors are not pages to audit
            url = normalize(doc.url)
            if url in seen:
                continue
            seen.add(url)
            if detect_from is None or url == start:
                detect_from = doc
            text = decode(doc.body, doc.content_type)
            crawl.pages.append(
                Page(
                    url=url,
                    final_url=url,
                    status=200,
                    chain=[Hop(url=url, status=200)],
                    headers=doc.headers,
                    content_type=doc.content_type,
                    size_bytes=len(doc.body),
                    source="start" if url == start else "link",
                    html=parse_html(text, url),
                )
            )
    if detect_from is None:
        details = "; ".join(skipped) or "no 200 HTML document found"
        raise ImportPagesError(f"no page could be imported: {details}")
    crawl.max_pages = len(crawl.pages)
    crawl.platform, crawl.platform_evidence = detect(
        decode(detect_from.body, detect_from.content_type), detect_from.headers
    )
    return crawl, skipped


def _validate(paths: list[str], urls: Mapping[str, str]) -> list[tuple[str, Path]]:
    """Exactly the files the user named: existing, regular, small, known type."""
    if not paths:
        raise ImportPagesError("paths is empty")
    if len(paths) > MAX_FILES:
        raise ImportPagesError(f"at most {MAX_FILES} files per import")
    outside = [key for key in urls if key not in paths]
    if outside:
        raise ImportPagesError(f"urls has files not in paths: {', '.join(outside)}")
    files: list[tuple[str, Path]] = []
    for given in dict.fromkeys(paths):
        path = Path(given).expanduser()
        if path.is_symlink():
            raise ImportPagesError(f"{given}: symbolic links are not imported")
        if not path.is_file():
            raise ImportPagesError(f"{given}: file not found")
        suffix = path.suffix.lower()
        if suffix not in HTML_SUFFIXES and suffix != ".har":
            raise ImportPagesError(f"{given}: only .html, .htm and .har files")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ImportPagesError(
                f"{given}: larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"
            )
        files.append((given, path))
    return files


def _html_doc(
    given: str, data: bytes, mapped: str | None, start: str, skipped: list[str]
) -> list[_Doc]:
    """A saved page; its URL is the explicit mapping, else canonical, else og:url."""
    url = mapped
    if url is None:
        parsed = parse_html(decode(data, ""), start)
        url = parsed.canonicals[0] if parsed.canonicals else parsed.og.get("og:url")
    if not url or not url.startswith(("http://", "https://")):
        skipped.append(f"{given}: no canonical or og:url; pass its URL in 'urls'")
        return []
    return [_Doc(normalize(url), 200, {}, "text/html", data, saved_file=True)]


def _har_docs(given: str, data: bytes, skipped: list[str]) -> list[_Doc]:
    """GET document responses from a DevTools HAR export."""
    try:
        entries = json.loads(data)["log"]["entries"]
    except (ValueError, KeyError, TypeError):
        raise ImportPagesError(f"{given}: not a HAR file (log.entries)") from None
    docs: list[_Doc] = []
    for entry in entries if isinstance(entries, list) else []:
        doc = _har_doc(entry)
        if doc is None:
            continue
        if not doc.body and doc.status == 200:
            skipped.append(f"{given}: {doc.url}: response body not saved in the HAR")
            continue
        docs.append(doc)
    return docs


def _har_doc(entry: Any) -> _Doc | None:
    try:
        request, response = entry["request"], entry["response"]
        if request["method"].upper() != "GET":
            return None
        url, status = str(request["url"]), int(response["status"])
        headers = _har_headers(response.get("headers", []))
        content = response.get("content", {})
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    content_type = headers.get("content-type") or str(content.get("mimeType", ""))
    resource_type = entry.get("_resourceType")
    if resource_type not in (None, "document") or not is_html(content_type):
        return None
    return _Doc(url, status, headers, content_type, _har_body(content))


def _har_headers(items: Iterable[Any]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for item in items:
        name, value = str(item["name"]).lower(), str(item["value"])
        merged[name] = f"{merged[name]}, {value}" if name in merged else value
    return merged


def _har_body(content: Mapping[str, Any]) -> bytes:
    text = content.get("text") or ""
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text, validate=False)
        except (binascii.Error, ValueError):
            return b""
    return str(text).encode("utf-8")
