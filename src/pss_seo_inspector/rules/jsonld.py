"""Shared JSON-LD helpers: parse every block once and flatten it into typed nodes.

This module defines no rules; the SD-* rules in ``structured.py`` use it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..models import Page
from ..urlutil import snip

Node = dict[str, Any]
# Children under these keys describe the page's own entity, so they stay top-level
# (WebPage.mainEntity → Product, ProductGroup.hasVariant → Product).
KEEP_TOP_KEYS = {"mainEntity", "hasVariant"}


@dataclass(frozen=True)
class Block:
    """One ``<script type=application/ld+json>`` block."""

    index: int
    raw: str
    error: str | None = None


@dataclass(frozen=True)
class FlatNode:
    """A typed JSON-LD object. ``top`` is False for objects nested in another
    typed object (offers, brand, publisher, ItemList items, ...)."""

    node: Node
    types: frozenset[str]
    top: bool
    block: int


@dataclass
class PageData:
    blocks: list[Block] = field(default_factory=list)
    nodes: list[FlatNode] = field(default_factory=list)
    ids: dict[str, Node] = field(default_factory=dict)

    @property
    def errors(self) -> list[Block]:
        return [b for b in self.blocks if b.error is not None]

    def of_type(self, *types: str, top_only: bool = False) -> list[Node]:
        wanted = set(types)
        return [
            n.node for n in self.nodes if n.types & wanted and (n.top or not top_only)
        ]

    def resolve(self, value: Any) -> Any:
        """Replace a bare ``{"@id": ...}`` reference with the node it names."""
        if isinstance(value, dict) and set(value) == {"@id"}:
            return self.ids.get(str(value["@id"]), value)
        return value


@lru_cache(maxsize=2048)
def _load(raw: str) -> tuple[Any, str | None]:
    try:
        return json.loads(raw), None
    except ValueError as exc:
        return None, str(exc)


def types_of(node: Node) -> frozenset[str]:
    value = node.get("@type")
    if isinstance(value, str):
        return frozenset({_short(value)})
    if isinstance(value, list):
        return frozenset(_short(str(v)) for v in value)
    return frozenset()


def _short(type_name: str) -> str:
    """``https://schema.org/Product`` → ``Product``."""
    return type_name.rstrip("/").rsplit("/", 1)[-1]


def page_data(page: Page) -> PageData:
    data = PageData()
    if page.html is None:
        return data
    for index, raw in enumerate(page.html.jsonld):
        value, error = _load(raw)
        data.blocks.append(Block(index=index, raw=raw, error=error))
        if error is None:
            _flatten(value, data, index, top=True)
    return data


def _flatten(value: Any, data: PageData, block: int, *, top: bool) -> None:
    if isinstance(value, list):
        for item in value:
            _flatten(item, data, block, top=top)
        return
    if not isinstance(value, dict):
        return
    types = types_of(value)
    if types:
        data.nodes.append(FlatNode(node=value, types=types, top=top, block=block))
        node_id = value.get("@id")
        if isinstance(node_id, str) and len(value) > 1:
            data.ids.setdefault(node_id, value)
    for key, child in value.items():
        if key == "@graph":
            _flatten(child, data, block, top=top)
        elif isinstance(child, dict | list):
            keep = top and (not types or key in KEEP_TOP_KEYS)
            _flatten(child, data, block, top=keep)


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def present(value: Any) -> bool:
    """True for a non-empty value (0 counts as present: free products)."""
    if value is None:
        return False
    if isinstance(value, str | list | dict):
        return bool(value.strip() if isinstance(value, str) else value)
    return True


def node_snippet(node: Node) -> str:
    return snip(json.dumps(node, ensure_ascii=False, separators=(",", ":")))
