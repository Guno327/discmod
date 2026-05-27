import subprocess
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discmod.models import DependencyRef, PackConfig, ResolvedVersion
from discmod.packwiz import (
    PackwizError,
    _side,
    read_current_pack,
    read_pack_config,
    run_packwiz_export,
    run_packwiz_refresh,
    write_mod_entry,
)

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


# --- read_current_pack ---

_MOD_TOML = """
name = "Sodium"
filename = "sodium-1.0.jar"
side = "client"

[download]
url = "https://example.com/sodium.jar"
hash-format = "sha512"
hash = "abc123"

[update]
[update.modrinth]
mod-id = "AANobbMI"
version = "xyz789"
"""


def test_read_current_pack_no_mods_dir(tmp_path):
    assert read_current_pack(tmp_path) == []


def test_read_current_pack_empty_mods_dir(tmp_path):
    (tmp_path / "mods").mkdir()
    assert read_current_pack(tmp_path) == []


def test_read_current_pack_with_mod(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "sodium.pw.toml").write_text(_MOD_TOML)
    result = read_current_pack(tmp_path)
    assert len(result) == 1
    assert result[0].slug == "sodium"
    assert result[0].project_id == "AANobbMI"
    assert result[0].version_id == "xyz789"
    assert result[0].title == "Sodium"


def test_read_current_pack_multiple_mods(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "sodium.pw.toml").write_text(_MOD_TOML)
    (mods / "lithium.pw.toml").write_text(_MOD_TOML.replace("Sodium", "Lithium").replace("AANobbMI", "lithium-id"))
    result = read_current_pack(tmp_path)
    assert len(result) == 2
    slugs = {m.slug for m in result}
    assert slugs == {"sodium", "lithium"}


# --- run_packwiz_refresh ---

def test_run_packwiz_refresh_success(tmp_path):
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        run_packwiz_refresh(tmp_path)  # must not raise


def test_run_packwiz_refresh_failure(tmp_path):
    with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="oops")):
        with pytest.raises(PackwizError, match="packwiz refresh failed"):
            run_packwiz_refresh(tmp_path)


# --- run_packwiz_export ---

def test_run_packwiz_export_with_explicit_output(tmp_path):
    out = tmp_path / "pack.mrpack"
    out.write_bytes(b"fake")
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        result = run_packwiz_export(tmp_path, output_path=out)
    assert result == out


def test_run_packwiz_export_finds_mrpack(tmp_path):
    mrpack = tmp_path / "pack-0.1.mrpack"
    mrpack.write_bytes(b"fake")
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        result = run_packwiz_export(tmp_path)
    assert result == mrpack


def test_run_packwiz_export_no_mrpack_raises(tmp_path):
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        with pytest.raises(PackwizError, match="no .mrpack found"):
            run_packwiz_export(tmp_path)


def test_run_packwiz_export_failure(tmp_path):
    with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="export failed")):
        with pytest.raises(PackwizError, match="packwiz modrinth export failed"):
            run_packwiz_export(tmp_path)
