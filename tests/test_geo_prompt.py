"""The /geo MCP prompt: pss measures, the installed seo-geo skill judges."""

from __future__ import annotations

from importlib.metadata import version
from typing import Any

import pytest

from pss_seo_inspector import server


async def _prompt_text(args: dict[str, Any]) -> str:
    from mcp import Client

    async with Client(server.mcp) as client:
        result = await client.get_prompt("geo", args)
    return "\n".join(m.content.text for m in result.messages)


@pytest.mark.anyio
async def test_geo_prompt_is_listed() -> None:
    from mcp import Client

    async with Client(server.mcp) as client:
        prompts = await client.list_prompts()
    names = [p.name for p in prompts.prompts]
    assert "geo" in names


@pytest.mark.anyio
async def test_geo_prompt_runs_audit_then_uses_installed_seo_geo() -> None:
    text = await _prompt_text({"url": "https://shop.test"})
    assert "audit_site" in text and "https://shop.test" in text
    assert "seo-geo" in text and "claude-seo" in text
    assert "GEO.md" in text
    assert f"pss-seo-inspector {version('pss-seo-inspector')}" in text
    # no rules of our own: stop when the skill is missing
    assert "not installed" in text and "stop" in text.lower()
    assert text.index("audit_site") < text.index("seo-geo")


@pytest.mark.anyio
async def test_geo_prompt_reuses_a_saved_crawl() -> None:
    text = await _prompt_text({"crawl_id": "shop.test/2026-10-12"})
    assert 'crawl_id="shop.test/2026-10-12"' in text


@pytest.mark.anyio
async def test_geo_prompt_needs_url_or_crawl_id() -> None:
    from mcp import Client
    from mcp.shared.exceptions import MCPError

    async with Client(server.mcp) as client:
        with pytest.raises(MCPError):
            await client.get_prompt("geo", {})
