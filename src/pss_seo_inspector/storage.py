"""Where reports and site configs live, and crawl/findings persistence."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .models import Crawl, Finding

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_FILES = ("findings.json", "report.md", "urls.csv")
CRAWL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*/\d{4}-\d{2}-\d{2}$")


class UnknownCrawlError(Exception):
    """No saved crawl for the given crawl_id."""


def reports_dir() -> Path:
    """``$PSS_SEO_REPORTS_DIR``, else ``reports/`` in the project checkout."""
    env = os.environ.get("PSS_SEO_REPORTS_DIR")
    return Path(env).expanduser() if env else PROJECT_ROOT / "reports"


def sites_dir() -> Path:
    """``$PSS_SEO_SITES_DIR``, else ``sites/`` in the project checkout."""
    env = os.environ.get("PSS_SEO_SITES_DIR")
    return Path(env).expanduser() if env else PROJECT_ROOT / "sites"


def crawl_dir(crawl_id: str) -> Path:
    if not CRAWL_ID_RE.match(crawl_id):
        raise UnknownCrawlError(
            f"Unknown crawl_id '{crawl_id}' (expected '<domain>/<YYYY-MM-DD>')"
        )
    return reports_dir() / crawl_id


def save_crawl(crawl: Crawl) -> Path:
    directory = crawl_dir(crawl.crawl_id)
    directory.mkdir(parents=True, exist_ok=True)
    for stale in REPORT_FILES:  # a same-day recrawl invalidates the old audit
        (directory / stale).unlink(missing_ok=True)
    path = directory / "crawl.json"
    path.write_text(crawl.model_dump_json(indent=1), encoding="utf-8")
    return path


def load_crawl(crawl_id: str) -> Crawl:
    path = crawl_dir(crawl_id) / "crawl.json"
    if not path.is_file():
        raise UnknownCrawlError(f"Unknown crawl_id '{crawl_id}': no crawl.json")
    return Crawl.model_validate_json(path.read_text(encoding="utf-8"))


def load_findings(crawl_id: str) -> list[Finding]:
    path = crawl_dir(crawl_id) / "findings.json"
    if not path.is_file():
        raise UnknownCrawlError(
            f"Unknown crawl_id '{crawl_id}': no findings.json (run audit_site first)"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Finding.model_validate(item) for item in data]
