import tomllib
from pathlib import Path

import pytest

from discmod.models import PackConfig, ResolvedVersion, DependencyRef
from discmod.packwiz import _side, read_pack_config, write_mod_entry

SAMPLE_PACK_TOML = """
name = "test-pack"
author = "test"
version = "0.1.0"
pack-format = "packwiz:1.1.0"

[index]
file = "index.toml"
hash-format = "sha256"
hash = "abc123"

[versions]
minecraft = "1.21.1"
neoforge = "21.1.95"
"""


def test_read_pack_config(tmp_path):
    (tmp_path / "pack.toml").write_text(SAMPLE_PACK_TOML)
    pack = read_pack_config(tmp_path)
    assert pack.mc_version == "1.21.1"
    assert pack.loader == "neoforge"
    assert pack.loader_version == "21.1.95"


def test_side_client_only():
    assert _side("required", "unsupported") == "client"


def test_side_server_only():
    assert _side("unsupported", "required") == "server"


def test_side_both():
    assert _side("optional", "optional") == "both"
    assert _side("required", "required") == "both"
    assert _side("required", "optional") == "both"


def test_write_mod_entry(tmp_path):
    (tmp_path / "mods").mkdir()
    project = {"title": "Sodium", "slug": "sodium"}
    resolved = ResolvedVersion(
        project_id="AANobbMI",
        version_id="xyz789",
        version_number="0.6.0+mc1.21",
        filename="sodium-neoforge-0.6.0+mc1.21.jar",
        download_url="https://cdn.modrinth.com/data/sodium.jar",
        sha512="abc" * 43,
        sha1="def" * 14,
        file_size=1234567,
        dependencies=(),
        client_side="required",
        server_side="unsupported",
    )
    path = write_mod_entry("sodium", project, resolved, tmp_path)
    assert path.exists()
    data = tomllib.loads(path.read_text())
    assert data["name"] == "Sodium"
    assert data["side"] == "client"
    assert data["download"]["hash-format"] == "sha512"
    assert data["update"]["modrinth"]["mod-id"] == "AANobbMI"
    assert data["update"]["modrinth"]["version"] == "xyz789"
