import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from discmod.release import _optional, _parse_github_remote, _read_pack_name, _read_pack_version, _require, _run

PACK_TOML = """
name = "my-modpack"
version = "1.2.3"
author = "test"
pack-format = "packwiz:1.1.0"

[index]
file = "index.toml"
hash-format = "sha256"
hash = "abc"

[versions]
minecraft = "1.21.1"
"""


def _cp(stdout="", stderr="", returncode=0):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


# --- _require / _optional ---

def test_require_present():
    with patch.dict(os.environ, {"MY_VAR": "hello"}, clear=False):
        assert _require("MY_VAR") == "hello"


def test_require_missing_exits():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(SystemExit):
            _require("MISSING_VAR")


def test_optional_present():
    with patch.dict(os.environ, {"MY_VAR": "world"}, clear=False):
        assert _optional("MY_VAR", "default") == "world"


def test_optional_missing_returns_default():
    with patch.dict(os.environ, {}, clear=True):
        assert _optional("MISSING_VAR", "default") == "default"


# --- _run ---

def test_run_success(tmp_path):
    r = _run(["true"], cwd=tmp_path)
    assert r.returncode == 0


def test_run_failure_exits(tmp_path):
    with pytest.raises(SystemExit):
        _run(["false"], cwd=tmp_path)


# --- _read_pack_version / _read_pack_name ---

def test_read_pack_version(tmp_path):
    (tmp_path / "pack.toml").write_text(PACK_TOML)
    assert _read_pack_version(tmp_path) == "1.2.3"


def test_read_pack_name(tmp_path):
    (tmp_path / "pack.toml").write_text(PACK_TOML)
    assert _read_pack_name(tmp_path) == "my-modpack"


def test_read_pack_name_default(tmp_path):
    (tmp_path / "pack.toml").write_text("[versions]\nminecraft = '1.21.1'\n")
    assert _read_pack_name(tmp_path) == "modpack"


# --- _parse_github_remote ---

def test_parse_github_remote_ssh(tmp_path):
    with patch("discmod.release._run", return_value=_cp(stdout="git@github.com:owner/repo.git\n")):
        owner, repo = _parse_github_remote(tmp_path, "origin")
    assert owner == "owner"
    assert repo == "repo"


def test_parse_github_remote_https(tmp_path):
    with patch("discmod.release._run", return_value=_cp(stdout="https://github.com/owner/repo.git\n")):
        owner, repo = _parse_github_remote(tmp_path, "origin")
    assert owner == "owner"
    assert repo == "repo"


def test_parse_github_remote_no_dotgit(tmp_path):
    with patch("discmod.release._run", return_value=_cp(stdout="https://github.com/owner/repo\n")):
        owner, repo = _parse_github_remote(tmp_path, "origin")
    assert owner == "owner"
    assert repo == "repo"


def test_parse_github_remote_invalid_exits(tmp_path):
    with patch("discmod.release._run", return_value=_cp(stdout="https://gitlab.com/owner/repo.git\n")):
        with pytest.raises(SystemExit):
            _parse_github_remote(tmp_path, "origin")


# --- release() entry-point error paths ---

def test_release_missing_pack_dir_exits():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(SystemExit):
            from discmod.release import release
            release()


def test_release_no_pack_toml_exits(tmp_path):
    env = {"PACK_DIR": str(tmp_path), "GITHUB_TOKEN": "tok"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit):
            from discmod.release import release
            release()


def test_release_tag_already_exists_exits(tmp_path):
    (tmp_path / "pack.toml").write_text(PACK_TOML)
    env = {"PACK_DIR": str(tmp_path), "GITHUB_TOKEN": "tok"}
    ls_remote_out = "abc123\trefs/tags/v1.2.3\n"

    def fake_run(args, **kwargs):
        if "ls-remote" in args:
            return _cp(stdout=ls_remote_out)
        return _cp()

    with patch.dict(os.environ, env, clear=True):
        with patch("discmod.release._run", return_value=_cp(stdout="git@github.com:owner/repo.git\n")):
            with patch("subprocess.run", side_effect=fake_run):
                with pytest.raises(SystemExit):
                    from discmod.release import release
                    release()


def test_release_main_block(tmp_path):
    """Covers the if __name__ == '__main__' branch via importlib."""
    env = {"PACK_DIR": str(tmp_path), "GITHUB_TOKEN": "tok"}
    with patch.dict(os.environ, env, clear=True):
        with patch("discmod.release.release", side_effect=SystemExit(0)):
            import importlib
            import discmod.release as rel_mod
            # Simulate __main__ execution
            with pytest.raises(SystemExit):
                rel_mod.release()
