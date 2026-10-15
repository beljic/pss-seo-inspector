"""Polite, read-only HTTP client: GET/HEAD only, rate limited, robots-aware."""

from __future__ import annotations

import asyncio
import posixpath
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal, Protocol
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx2

from .models import Hop, Resource, RobotsFile
from .robots import RobotsTxt, from_status
from .urlutil import absolutize, origin_of

USER_AGENT = "pss-seo-inspector/1.0"
ROBOTS_TOKEN = "pss-seo-inspector"
DEFAULT_RATE = 2.0
TIMEOUT_SECONDS = 10.0
MAX_HOPS = 10
MAX_BODY_BYTES = 5 * 1024 * 1024
ROBOTS_MAX_BYTES = 1024 * 1024
IMAGE_RANGE_BYTES = 64 * 1024
RATE_LIMIT_BACKOFF_SECONDS = 5.0
MAX_RETRY_AFTER_SECONDS = 3600.0

Method = Literal["GET", "HEAD"]

READONLY_BLOCKED = "read-only policy:"
_ACTION_KEYS = {
    "add-to-cart",
    "addtocart",
    "remove-item",
    "undo-item",
    "update-cart",
    "empty-cart",
    "clear-cart",
    "logout",
    "signout",
    "subscribe",
    "unsubscribe",
    "wc-ajax",
}
_ACTION_VERBS = {
    "add",
    "additem",
    "addtocart",
    "remove",
    "delete",
    "update",
    "clear",
    "empty",
    "logout",
    "signout",
    "subscribe",
    "unsubscribe",
    "placeorder",
    "buy",
    "purchase",
    "submit",
    "save",
    "cancel",
}


def _decoded(value: str) -> str:
    """Recognize encoded action names without changing the URL we would request."""
    for _ in range(4):
        decoded = unquote(value)
        if decoded == value:
            return value
        value = decoded
    return value


def _action_url_reason(url: str) -> str | None:
    """Conservatively block known actions, not prove arbitrary GET handlers safe.

    Custom endpoints with undisclosed side effects cannot be inferred from a URL.
    This policy applies before requests, including redirects and robots probes.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "invalid URL"
    path = posixpath.normpath(_decoded(parts.path).replace("\\", "/").lower())
    segments = [s.split(";", 1)[0] for s in path.split("/") if s]
    segments = [s.split(".", 1)[0].replace("_", "-") for s in segments]
    if segments and segments[0] == "index":
        segments = segments[1:]
    if (
        len(segments) > 1
        and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", segments[0])
        and segments[1] in {"cart", "account", "checkout", "customer", "wishlist"}
    ):
        segments = segments[1:]
    # Magento may prefix its controller routes with a configured store code.
    if (
        len(segments) > 1
        and segments[1] in {"checkout", "customer", "wishlist"}
        and segments[0] not in {"products", "collections", "pages", "blogs"}
    ):
        segments = segments[1:]
    if segments and segments[0] in {"cart", "wishlist"}:
        if any(s != "index" for s in segments[1:]):
            return f"{segments[0]} action or permalink"
    if segments[:2] == ["checkout", "cart"]:
        if any(s != "index" for s in segments[2:]):
            return "cart controller action"
    if segments and segments[0] in {"account", "customer", "session", "members"}:
        if set(segments[1:]) & {"logout", "signout", "subscribe", "unsubscribe"}:
            return "account action"
    if segments in [["logout"], ["signout"], ["subscribe"], ["unsubscribe"]]:
        return "account action"
    api_path = segments[1:] if segments[:1] == ["wp-json"] else segments
    if api_path[:2] == ["wc", "store"] and "cart" in api_path:
        cart_index = api_path.index("cart")
        if api_path[cart_index + 1 :]:
            return "cart REST action"
    query = [
        (_decoded(k).lower().replace("_", "-").rstrip("[]"), _decoded(v))
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    if any(k == "controller" and v.lower() == "cart" for k, v in query):
        if any(k in {"add", "delete", "remove", "update", "change"} for k, _ in query):
            return "cart query controller action"
    for key, value in query:
        if key in _ACTION_KEYS:
            return f"action query parameter '{key}'"
        if key in {"action", "do", "task", "cmd", "command"}:
            words = set(re.split(r"[^a-z]+", _decoded(value).lower()))
            if words & _ACTION_VERBS:
                return f"action selector '{key}'"
        if key == "rest-route" and _action_url_reason(
            "https://unused.test/" + value.lstrip("/")
        ):
            return "action REST route"
    return None


class Clock(Protocol):
    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class RateLimiter:
    """At most ``rate`` requests per second, measured on ``clock``."""

    def __init__(self, rate: float, clock: Clock) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self.interval = 1.0 / rate
        self.clock = clock
        self._last: float | None = None

    async def wait(self, min_gap: float = 0.0) -> None:
        """Wait until max(1/rate, ``min_gap``) has passed since the last request."""
        now = self.clock.monotonic()
        if self._last is not None:
            due = self._last + max(self.interval, min_gap)
            if due > now:
                await self.clock.sleep(due - now)
                now = self.clock.monotonic()
        self._last = now


@dataclass
class FetchResult:
    resource: Resource
    body: bytes = b""

    def text(self) -> str:
        return decode(self.body, self.resource.content_type)


_META_CHARSET = re.compile(
    rb"<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.I
)


def decode(body: bytes, content_type: str) -> str:
    """Charset from the Content-Type header, else <meta charset> in 1024 bytes."""
    charset = ""
    for part in content_type.split(";"):
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "charset" and value:
            charset = value.strip("\"' ")
    if not charset:
        match = _META_CHARSET.search(body[:1024])
        charset = match.group(1).decode("ascii") if match else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def is_html(content_type: str) -> bool:
    ctype = content_type.split(";", 1)[0].strip().lower()
    return ctype in ("text/html", "application/xhtml+xml") or ctype == ""


class Fetcher:
    """The only component that talks to the network."""

    def __init__(
        self,
        *,
        rate: float = DEFAULT_RATE,
        transport: httpx2.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self.clock = clock or SystemClock()
        self.limiter = RateLimiter(rate, self.clock)
        self.rate_limited = False
        self.client = httpx2.AsyncClient(
            transport=transport,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=False,
        )
        self._robots: dict[str, tuple[RobotsFile, RobotsTxt]] = {}

    async def aclose(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def robots(self, url: str) -> tuple[RobotsFile, RobotsTxt]:
        """robots.txt of the URL's origin (cached; fetching it is always allowed)."""
        origin = origin_of(url)
        cached = self._robots.get(origin)
        if cached is not None:
            return cached
        result = await self._fetch(
            f"{origin}/robots.txt",
            method="GET",
            read_body=True,
            max_body=ROBOTS_MAX_BYTES,
            respect_robots=False,
        )
        res = result.resource
        try:
            result.body.decode("utf-8")
            utf8_ok = True
        except UnicodeDecodeError:
            utf8_ok = False
        text = result.body.decode("utf-8", errors="replace")
        robots_file = RobotsFile(**res.model_dump(), text=text, utf8_ok=utf8_ok)
        parsed = from_status(res.status, text, res.error)
        self._robots[origin] = (robots_file, parsed)
        return robots_file, parsed

    def _crawl_delay(self, url: str) -> float:
        """robots.txt Crawl-delay for our group on this origin (if already known)."""
        cached = self._robots.get(origin_of(url))
        return cached[1].crawl_delay(ROBOTS_TOKEN) if cached else 0.0

    async def allowed(self, url: str) -> bool:
        _, parsed = await self.robots(url)
        return parsed.allowed(ROBOTS_TOKEN, url)

    async def get(
        self,
        url: str,
        *,
        method: Method = "GET",
        read_body: bool = True,
        html_only: bool = False,
        max_body: int = MAX_BODY_BYTES,
        range_bytes: int | None = None,
    ) -> FetchResult:
        return await self._fetch(
            url,
            method=method,
            read_body=read_body,
            html_only=html_only,
            max_body=max_body,
            range_bytes=range_bytes,
            respect_robots=True,
        )

    async def _fetch(
        self,
        url: str,
        *,
        method: Method,
        read_body: bool,
        respect_robots: bool,
        html_only: bool = False,
        max_body: int = MAX_BODY_BYTES,
        range_bytes: int | None = None,
    ) -> FetchResult:
        res = Resource(url=url, final_url=url)
        body = b""
        if self.rate_limited:
            res.status = 429
            res.error = "crawl stopped after repeated HTTP 429 responses"
            return FetchResult(resource=res)
        current = url
        seen: set[str] = set()
        headers = {"Range": f"bytes=0-{range_bytes - 1}"} if range_bytes else {}
        retries_after_429 = 0
        while True:
            reason = _action_url_reason(current)
            if method not in {"GET", "HEAD"}:
                reason = "only GET and HEAD are allowed"
            if reason:
                res.error = f"{READONLY_BLOCKED} {reason}"
                break
            if respect_robots and not await self.allowed(current):
                res.robots_blocked = True
                break
            seen.add(current)
            await self.limiter.wait(self._crawl_delay(current))
            try:
                async with self.client.stream(
                    method, current, headers=headers
                ) as response:
                    status = response.status_code
                    if status == 429:
                        if retries_after_429:
                            self.rate_limited = True
                            res.status = 429
                            res.headers = _headers(response.headers)
                            res.error = (
                                "crawl stopped after repeated HTTP 429 responses"
                            )
                            break
                        retries_after_429 += 1
                        self.limiter.interval = max(
                            self.limiter.interval, RATE_LIMIT_BACKOFF_SECONDS
                        )
                        delay = _retry_after_seconds(
                            response.headers.get("retry-after")
                        )
                        await self.clock.sleep(delay)
                        continue
                    location = response.headers.get("location")
                    if 300 <= status < 400 and location:
                        target = absolutize(current, location) or location
                        res.chain.append(
                            Hop(url=current, status=status, location=target)
                        )
                        if target in seen:
                            res.error = "redirect loop"
                            res.status = status
                            break
                        if len(res.chain) >= MAX_HOPS:
                            res.error = f"more than {MAX_HOPS} redirects"
                            res.status = status
                            break
                        current = target
                        continue
                    res.chain.append(Hop(url=current, status=status))
                    res.status = status
                    res.headers = _headers(response.headers)
                    res.content_type = response.headers.get("content-type", "")
                    want = read_body and method == "GET"
                    if html_only and not is_html(res.content_type):
                        want = False
                    if want:
                        body, truncated = await _read_capped(response, max_body)
                        res.truncated = truncated
                    res.size_bytes = len(body)
                    break
            except httpx2.TimeoutException:
                res.error = "timeout"
                break
            except (httpx2.HTTPError, httpx2.InvalidURL) as exc:
                res.error = f"{type(exc).__name__}: {exc}"[:200]
                break
        res.final_url = current
        return FetchResult(resource=res, body=body)


def _retry_after_seconds(value: str | None) -> float:
    """Parse Retry-After as seconds or an HTTP date, with a bounded fallback."""
    if value:
        try:
            return max(0.0, min(float(value), MAX_RETRY_AFTER_SECONDS))
        except ValueError:
            try:
                when = parsedate_to_datetime(value)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                return max(
                    0.0,
                    min(
                        (when - datetime.now(UTC)).total_seconds(),
                        MAX_RETRY_AFTER_SECONDS,
                    ),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return RATE_LIMIT_BACKOFF_SECONDS


def _headers(headers: httpx2.Headers) -> dict[str, str]:
    merged: dict[str, str] = {}
    for key, value in headers.multi_items():
        k = key.lower()
        merged[k] = f"{merged[k]}, {value}" if k in merged else value
    return merged


async def _read_capped(response: httpx2.Response, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            return b"".join(chunks)[:limit], True
    return b"".join(chunks), False
