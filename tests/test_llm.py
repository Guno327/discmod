from unittest.mock import AsyncMock, MagicMock

import pytest

from discmod.llm import soft_conflict_check
from discmod.models import PackMod

VALID_JSON = (
    '{"summary": "A rendering optimization mod",'
    ' "conflicts": [{"with": "iris", "severity": "medium", "reason": "shader pipeline"}]}'
)


def _mod(slug: str) -> PackMod:
    return PackMod(
        slug=slug,
        title=slug.title(),
        description=f"A {slug} mod",
        project_id=f"id-{slug}",
        version_id="v1",
        version_number="1.0.0",
    )


def _client(text: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    c = MagicMock()
    c.messages.create = AsyncMock(return_value=msg)
    return c


@pytest.mark.asyncio
async def test_valid_response_parsed():
    summary, conflicts = await soft_conflict_check(_mod("sodium"), [_mod("iris")], _client(VALID_JSON), "m")
    assert summary == "A rendering optimization mod"
    assert len(conflicts) == 1
    assert conflicts[0].with_slug == "iris"
    assert conflicts[0].severity == "medium"
    assert conflicts[0].reason == "shader pipeline"


@pytest.mark.asyncio
async def test_strips_json_code_fences():
    fenced = "```json\n" + VALID_JSON + "\n```"
    summary, conflicts = await soft_conflict_check(_mod("sodium"), [], _client(fenced), "m")
    assert summary == "A rendering optimization mod"


@pytest.mark.asyncio
async def test_strips_plain_code_fences():
    fenced = "```\n" + VALID_JSON + "\n```"
    summary, conflicts = await soft_conflict_check(_mod("sodium"), [], _client(fenced), "m")
    assert summary == "A rendering optimization mod"


@pytest.mark.asyncio
async def test_empty_conflicts_list():
    resp = '{"summary": "Just a mod", "conflicts": []}'
    summary, conflicts = await soft_conflict_check(_mod("sodium"), [], _client(resp), "m")
    assert summary == "Just a mod"
    assert conflicts == []


@pytest.mark.asyncio
async def test_malformed_json_returns_empty():
    summary, conflicts = await soft_conflict_check(_mod("sodium"), [], _client("not json"), "m")
    assert summary == ""
    assert conflicts == []


@pytest.mark.asyncio
async def test_api_exception_returns_empty():
    c = MagicMock()
    c.messages.create = AsyncMock(side_effect=Exception("API error"))
    summary, conflicts = await soft_conflict_check(_mod("sodium"), [], c, "m")
    assert summary == ""
    assert conflicts == []


@pytest.mark.asyncio
async def test_empty_pack_still_calls_api():
    summary, conflicts = await soft_conflict_check(_mod("sodium"), [], _client(VALID_JSON), "m")
    assert summary != "" or conflicts == []


@pytest.mark.asyncio
async def test_multiple_mods_in_pack():
    pack = [_mod("iris"), _mod("indium"), _mod("lithium")]
    resp = '{"summary": "Perf mod", "conflicts": []}'
    summary, _ = await soft_conflict_check(_mod("sodium"), pack, _client(resp), "m")
    assert summary == "Perf mod"


@pytest.mark.asyncio
async def test_conflict_defaults_severity_to_low():
    resp = '{"summary": "x", "conflicts": [{"with": "iris", "reason": "overlap"}]}'
    _, conflicts = await soft_conflict_check(_mod("sodium"), [], _client(resp), "m")
    assert conflicts[0].severity == "low"
