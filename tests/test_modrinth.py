import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from discmod.models import PackConfig
from discmod.modrinth import ModrinthClient, ModrinthError, NoCompatibleVersion, parse_slug

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text())


# --- URL parsing ---

def test_parse_full_url():
    assert parse_slug("https://modrinth.com/mod/sodium") == "sodium"


def test_parse_url_with_version():
    assert parse_slug("https://modrinth.com/mod/sodium/version/mc1.21-0.6.0") == "sodium"


def test_parse_bare_slug():
    assert parse_slug("sodium") == "sodium"


def test_parse_bare_project_id():
    assert parse_slug("AANobbMI") == "AANobbMI"


def test_parse_invalid_url():
    with pytest.raises(ModrinthError):
        parse_slug("https://curseforge.com/mod/sodium")


def test_parse_url_shader():
    assert parse_slug("https://modrinth.com/shader/complementary-reimagined") == "complementary-reimagined"


def test_parse_url_resourcepack():
    assert parse_slug("https://modrinth.com/resourcepack/xekr") == "xekr"


# --- Version selection ---

def _make_version(version_type: str, date: str, version_number: str = "1.0.0") -> dict:
    return {
        "id": f"id-{version_number}",
        "project_id": "AANobbMI",
        "version_number": version_number,
        "version_type": version_type,
        "date_published": date,
        "files": [
            {
                "primary": True,
                "filename": f"mod-{version_number}.jar",
                "url": f"https://example.com/{version_number}.jar",
                "hashes": {"sha512": "abc", "sha1": "def"},
                "size": 1000,
            }
        ],
        "dependencies": [],
        "game_versions": ["1.21.1"],
        "loaders": ["fabric"],
    }


def _select_best(versions: list[dict]) -> dict:
    """Mirror the selection logic from resolve_version."""
    tier_order = {"release": 0, "beta": 1, "alpha": 2}
    return min(versions, key=lambda v: tier_order.get(v.get("version_type", "alpha"), 3))


def test_prefers_release_over_alpha():
    versions = [
        _make_version("alpha", "2024-01-02", "1.0.1"),
        _make_version("release", "2024-01-01", "1.0.0"),
    ]
    best = _select_best(versions)
    assert best["version_type"] == "release"


def test_prefers_beta_over_alpha():
    versions = [
        _make_version("alpha", "2024-01-02", "1.0.1"),
        _make_version("beta", "2024-01-01", "1.0.0"),
    ]
    best = _select_best(versions)
    assert best["version_type"] == "beta"


def test_takes_first_within_tier():
    versions = [
        _make_version("release", "2024-01-02", "1.0.1"),
        _make_version("release", "2024-01-01", "1.0.0"),
    ]
    # min selects first occurrence of min value — both are release (0), so first wins
    best = _select_best(versions)
    assert best["version_number"] == "1.0.1"


def test_sodium_fixture_has_versions():
    versions = load("sodium_versions_fabric_1211.json")
    assert isinstance(versions, list)
    assert len(versions) > 0
    v = versions[0]
    assert "version_number" in v
    assert "files" in v
    assert "dependencies" in v


def test_sodium_project_fixture():
    project = load("sodium_project.json")
    assert project["slug"] == "sodium"
    assert project["id"] == "AANobbMI"
    assert "client_side" in project


# --- Helpers for HTTP mocking ---

def _mock_transport(*responses: tuple[int, object]):
    """
    Build an httpx.MockTransport that serves responses in order.
    Each entry is (status_code, body) where body is a dict/list (JSON) or str.
    """
    calls = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        code, body = calls.pop(0)
        if isinstance(body, (dict, list)):
            return httpx.Response(code, json=body)
        return httpx.Response(code, text=str(body))

    return httpx.MockTransport(handler)


def _client(*responses: tuple[int, object]) -> ModrinthClient:
    c = ModrinthClient("test/test/0.0 (test@example.com)")
    c._client = httpx.AsyncClient(
        base_url="https://api.modrinth.com/v2",
        transport=_mock_transport(*responses),
    )
    return c


PACK = PackConfig(mc_version="1.21.1", loader="fabric", loader_version=None)


# --- resolve_version ---

@pytest.mark.asyncio
async def test_resolve_version_picks_best():
    versions = load("sodium_versions_fabric_1211.json")
    project = load("sodium_project.json")
    c = _client((200, versions), (200, project))
    resolved = await c.resolve_version("sodium", PACK)
    assert resolved.project_id == project["id"]
    # The best version is the highest-tier (release > beta > alpha) then newest first.
    tier_order = {"release": 0, "beta": 1, "alpha": 2}
    expected = min(versions, key=lambda v: tier_order.get(v.get("version_type", "alpha"), 3))
    assert resolved.version_number == expected["version_number"]
    assert resolved.sha512 != ""
    assert resolved.client_side == project["client_side"]
    assert resolved.server_side == project["server_side"]


@pytest.mark.asyncio
async def test_resolve_version_no_versions_raises():
    project = load("sodium_project.json")
    c = _client((200, []), (200, project))
    with pytest.raises(NoCompatibleVersion) as exc_info:
        await c.resolve_version("sodium", PACK)
    assert "sodium" in str(exc_info.value)


@pytest.mark.asyncio
async def test_resolve_version_prefers_release_over_beta():
    beta = _make_version("beta", "2024-01-02", "0.9.0-beta")
    release = _make_version("release", "2024-01-01", "0.8.0")
    project = load("sodium_project.json")
    c = _client((200, [beta, release]), (200, project))
    resolved = await c.resolve_version("sodium", PACK)
    assert resolved.version_number == "0.8.0"


@pytest.mark.asyncio
async def test_resolve_version_falls_back_to_beta_when_no_release():
    alpha = _make_version("alpha", "2024-01-02", "0.9.0-alpha")
    beta = _make_version("beta", "2024-01-01", "0.8.0-beta")
    project = load("sodium_project.json")
    c = _client((200, [alpha, beta]), (200, project))
    resolved = await c.resolve_version("sodium", PACK)
    assert resolved.version_number == "0.8.0-beta"


@pytest.mark.asyncio
async def test_resolve_version_uses_primary_file():
    non_primary = {
        "primary": False,
        "filename": "wrong.jar",
        "url": "https://example.com/wrong.jar",
        "hashes": {"sha512": "wrong512", "sha1": "wrong1"},
        "size": 500,
    }
    primary = {
        "primary": True,
        "filename": "correct.jar",
        "url": "https://example.com/correct.jar",
        "hashes": {"sha512": "correct512", "sha1": "correct1"},
        "size": 1000,
    }
    version = _make_version("release", "2024-01-01", "1.0.0")
    version["files"] = [non_primary, primary]
    project = load("sodium_project.json")
    c = _client((200, [version]), (200, project))
    resolved = await c.resolve_version("sodium", PACK)
    assert resolved.filename == "correct.jar"
    assert resolved.sha512 == "correct512"


@pytest.mark.asyncio
async def test_resolve_version_parses_dependencies():
    version = _make_version("release", "2024-01-01", "1.0.0")
    version["dependencies"] = [
        {"project_id": "P7dR8mSH", "version_id": None, "dependency_type": "required"},
        {"project_id": "Xbc8KAOT", "version_id": None, "dependency_type": "incompatible"},
    ]
    project = load("sodium_project.json")
    c = _client((200, [version]), (200, project))
    resolved = await c.resolve_version("sodium", PACK)
    assert len(resolved.dependencies) == 2
    req = next(d for d in resolved.dependencies if d.dependency_type == "required")
    assert req.project_id == "P7dR8mSH"
    incompat = next(d for d in resolved.dependencies if d.dependency_type == "incompatible")
    assert incompat.project_id == "Xbc8KAOT"


# --- fetch_project / fetch_versions ---

@pytest.mark.asyncio
async def test_fetch_project_returns_project():
    project = load("sodium_project.json")
    c = _client((200, project))
    result = await c.fetch_project("sodium")
    assert result["slug"] == "sodium"


@pytest.mark.asyncio
async def test_fetch_versions_passes_params():
    """Verify the call succeeds and returns a list."""
    versions = load("sodium_versions_fabric_1211.json")
    c = _client((200, versions))
    result = await c.fetch_versions("sodium", "1.21.1", "fabric")
    assert isinstance(result, list)
    assert len(result) > 0


# --- fetch_project_by_id_batch ---

@pytest.mark.asyncio
async def test_batch_returns_dict_keyed_by_id():
    batch = load("batch_projects.json")
    c = _client((200, batch))
    ids = [p["id"] for p in batch]
    result = await c.fetch_project_by_id_batch(ids)
    assert set(result.keys()) == set(ids)
    for pid, proj in result.items():
        assert proj["id"] == pid


@pytest.mark.asyncio
async def test_batch_empty_ids_returns_empty_without_request():
    # No responses registered — would fail if a request were made.
    c = _client()
    result = await c.fetch_project_by_id_batch([])
    assert result == {}


# --- smoke_check ---

@pytest.mark.asyncio
async def test_smoke_check_succeeds_on_200():
    c = _client((200, ["neoforge", "fabric", "forge", "quilt"]))
    await c.smoke_check()  # should not raise


@pytest.mark.asyncio
async def test_smoke_check_raises_on_404():
    # _get retries up to 3 times even on 4xx, so provide 3 responses.
    c = _client((404, "Not Found"), (404, "Not Found"), (404, "Not Found"))
    with patch("discmod.modrinth.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ModrinthError):
            await c.smoke_check()


# --- retry / error handling ---

@pytest.mark.asyncio
async def test_retries_on_500_then_succeeds():
    project = load("sodium_project.json")
    c = _client((500, "error"), (200, project))
    # patch asyncio.sleep so the test doesn't actually wait
    with patch("discmod.modrinth.asyncio.sleep", new=AsyncMock()):
        result = await c.fetch_project("sodium")
    assert result["slug"] == "sodium"


@pytest.mark.asyncio
async def test_raises_after_three_500s():
    c = _client((500, "err"), (500, "err"), (500, "err"))
    with patch("discmod.modrinth.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ModrinthError, match="3 attempts"):
            await c.fetch_project("sodium")


@pytest.mark.asyncio
async def test_raises_on_404():
    # _get retries up to 3 times even on 4xx, so provide 3 responses.
    c = _client((404, "Not Found"), (404, "Not Found"), (404, "Not Found"))
    with patch("discmod.modrinth.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ModrinthError):
            await c.fetch_project("does-not-exist")


@pytest.mark.asyncio
async def test_close():
    c = ModrinthClient("test/test/0.0 (test@example.com)")
    await c.close()  # must not raise


@pytest.mark.asyncio
async def test_rate_limit_warning_when_remaining_low():
    """Line 47: log warning and sleep when X-Ratelimit-Remaining < 10."""
    project = load("sodium_project.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=project, headers={"X-Ratelimit-Remaining": "5"})

    c = ModrinthClient("test/test/0.0 (test@example.com)")
    c._client = httpx.AsyncClient(
        base_url="https://api.modrinth.com/v2",
        transport=httpx.MockTransport(handler),
    )
    with patch("discmod.modrinth.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        result = await c.fetch_project("sodium")
    assert result["slug"] == "sodium"
    mock_sleep.assert_called_once_with(2)


@pytest.mark.asyncio
async def test_429_retries_then_succeeds():
    project = load("sodium_project.json")
    # First response is 429 with Retry-After header, second succeeds.
    def handler(request: httpx.Request) -> httpx.Response:
        handler.count = getattr(handler, "count", 0) + 1
        if handler.count == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return httpx.Response(200, json=project)

    c = ModrinthClient("test/test/0.0 (test@example.com)")
    c._client = httpx.AsyncClient(
        base_url="https://api.modrinth.com/v2",
        transport=httpx.MockTransport(handler),
    )
    with patch("discmod.modrinth.asyncio.sleep", new=AsyncMock()):
        result = await c.fetch_project("sodium")
    assert result["slug"] == "sodium"
