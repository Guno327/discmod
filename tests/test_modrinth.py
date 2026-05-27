import json
from pathlib import Path

import pytest

from discmod.models import PackConfig
from discmod.modrinth import ModrinthError, NoCompatibleVersion, parse_slug

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
