"""Optional per-site expectations, loaded from ``sites/<domain>.json``."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .storage import sites_dir


class SiteConfigError(Exception):
    """Invalid site config file (bad JSON or schema)."""


class KnownIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    rule_ids: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


def _check_regex(value: str) -> str:
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"invalid regex: {exc}") from exc
    return value


class Fact(BaseModel):
    """A fact that must read the same on every page (CONT-01).

    ``pattern`` is matched case-insensitively against each page's visible text; the
    value of a match is its groups joined by a space (or the whole match if it has no
    groups), lower-cased and whitespace-collapsed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    expected: str | None = None

    @field_validator("pattern")
    @classmethod
    def _valid_pattern(cls, value: str) -> str:
        return _check_regex(value)


class SiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand: str | None = None
    brand_wrong_spellings: list[str] = Field(default_factory=list)
    product_name_pattern: str | None = None
    noindex_urls: list[str] = Field(default_factory=list)
    known_issues: list[KnownIssue] = Field(default_factory=list)
    pagespeed_urls: list[str] = Field(default_factory=list)
    legacy_urls: list[str] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)

    @field_validator("product_name_pattern")
    @classmethod
    def _valid_regex(cls, value: str | None) -> str | None:
        return _check_regex(value) if value is not None else None


def load_site_config(domain: str, path: str | None = None) -> SiteConfig | None:
    """Load ``path`` if given, else ``sites/<domain>.json`` if it exists."""
    if path is not None:
        file = Path(path).expanduser()
        if not file.is_absolute() and not file.exists():
            file = sites_dir() / path
        if not file.is_file():
            raise SiteConfigError(f"Site config not found: {path}")
    else:
        file = sites_dir() / f"{domain}.json"
        if not file.is_file():
            return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SiteConfigError(f"Site config {file} is not valid JSON: {exc}") from None
    try:
        return SiteConfig.model_validate(data)
    except ValidationError as exc:
        raise SiteConfigError(f"Site config {file} is invalid: {exc}") from None
