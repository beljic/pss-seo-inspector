"""robots.txt parsing with Google's matching semantics.

``urllib.robotparser`` ignores the ``*`` and ``$`` wildcards that platforms such as
Shopify rely on (``/collections/*sort_by*``), so a crawler built on it would fetch
disallowed URLs. This module implements the documented Google behaviour instead:
groups per user-agent token (falling back to ``*``), longest-match wins, ``Allow``
wins ties.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .urlutil import path_and_query

SUPPORTED_DIRECTIVES = {"user-agent", "allow", "disallow", "sitemap"}


@dataclass
class RobotsRule:
    allow: bool
    pattern: str
    line: int

    def regex(self) -> re.Pattern[str]:
        anchored = self.pattern.endswith("$")
        body = self.pattern[:-1] if anchored else self.pattern
        expr = ".*".join(re.escape(part) for part in body.split("*"))
        return re.compile(expr + ("$" if anchored else ""))


@dataclass
class RobotsGroup:
    agents: list[str]
    rules: list[RobotsRule] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass
class RobotsTxt:
    groups: list[RobotsGroup] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    other_directives: list[tuple[int, str, str]] = field(default_factory=list)
    allow_all: bool = False
    disallow_all: bool = False

    def rules_for(self, token: str) -> list[RobotsRule]:
        """Rules of every group naming ``token``; otherwise those of ``*``."""
        token = token.lower()
        named = [g for g in self.groups if token in g.agents]
        chosen = named or [g for g in self.groups if "*" in g.agents]
        return [rule for group in chosen for rule in group.rules]

    def crawl_delay(self, token: str) -> float:
        """Crawl-delay of the group that applies to ``token`` (0 if none)."""
        token = token.lower()
        named = [g for g in self.groups if token in g.agents]
        chosen = named or [g for g in self.groups if "*" in g.agents]
        delays = [g.crawl_delay for g in chosen if g.crawl_delay is not None]
        return max(delays, default=0.0)

    def has_group(self, token: str) -> bool:
        return any(token.lower() in g.agents for g in self.groups)

    def allowed(self, token: str, url: str) -> bool:
        if self.disallow_all:
            return False
        if self.allow_all:
            return True
        target = path_and_query(url)
        if target == "/robots.txt":
            return True
        best: RobotsRule | None = None
        for rule in self.rules_for(token):
            if not rule.pattern:
                continue
            if rule.regex().match(target) is None:
                continue
            if (
                best is None
                or len(rule.pattern) > len(best.pattern)
                or (len(rule.pattern) == len(best.pattern) and rule.allow)
            ):
                best = rule
        return best is None or best.allow


def parse_robots(text: str) -> RobotsTxt:
    text = text.lstrip("\ufeff")
    robots = RobotsTxt()
    current: RobotsGroup | None = None
    last_was_agent = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        key = name.lower()
        if key == "user-agent":
            if current is None or not last_was_agent:
                current = RobotsGroup(agents=[])
                robots.groups.append(current)
            current.agents.append(value.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if key == "sitemap":
            robots.sitemaps.append(value)
        elif key in ("allow", "disallow"):
            if current is not None:
                current.rules.append(RobotsRule(key == "allow", value, number))
        else:
            robots.other_directives.append((number, name, value))
            if key == "crawl-delay" and current is not None:
                try:
                    current.crawl_delay = max(0.0, float(value))
                except ValueError:
                    pass
    return robots


def allow_all() -> RobotsTxt:
    return RobotsTxt(allow_all=True)


def disallow_all() -> RobotsTxt:
    return RobotsTxt(disallow_all=True)


def from_status(status: int | None, text: str, error: str | None = None) -> RobotsTxt:
    """Google: 2xx → parse; 4xx (except 429) or redirect limit → allow all;
    5xx, 429 or unreachable → disallow all."""
    if error is not None and "redirect" in error:
        return allow_all()
    if error is not None or status is None or status >= 500 or status == 429:
        return disallow_all()
    if 400 <= status < 500:
        return allow_all()
    return parse_robots(text)
