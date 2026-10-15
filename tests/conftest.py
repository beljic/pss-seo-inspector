from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pss_seo_inspector import server


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    reports = tmp_path / "reports"
    sites = tmp_path / "sites"
    sites.mkdir()
    monkeypatch.setenv("PSS_SEO_REPORTS_DIR", str(reports))
    monkeypatch.setenv("PSS_SEO_SITES_DIR", str(sites))
    monkeypatch.setattr(server, "transport", None)
    monkeypatch.setattr(server, "clock", None)
    monkeypatch.setattr(server, "today", None)
    yield tmp_path


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
