import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discmod.db import insert_proposal, open_db, transition_to_merging
from discmod.merge import (
    MergeBlocked,
    MergeFailed,
    MergePushFailed,
    _build_commit_body,
    execute_merge_add,
    execute_merge_remove,
)
from discmod.models import DependencyRef, ResolvedVersion, SoftConflict

PACK_TOML = """
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


def _resolved(project_id="AANobbMI", deps=()):
    return ResolvedVersion(
        project_id=project_id,
        version_id="xyz789",
        version_number="0.6.0+mc1.21",
        filename="sodium-0.6.0.jar",
        download_url="https://example.com/sodium.jar",
        sha512="a" * 128,
        sha1="b" * 40,
        file_size=1234,
        dependencies=tuple(deps),
        client_side="required",
        server_side="unsupported",
    )


def _proposal(message_id=1, slug="sodium"):
    return {
        "message_id": message_id,
        "slug": slug,
        "proposer_name": "Alice",
        "proposer_id": 42,
    }


def _modrinth(resolved=None):
    project = {"title": "Sodium", "slug": "sodium", "client_side": "required", "server_side": "unsupported"}
    m = MagicMock()
    m.fetch_project = AsyncMock(return_value=project)
    m.resolve_version = AsyncMock(return_value=resolved or _resolved())
    return m


@pytest.fixture
def pack_dir(tmp_path):
    (tmp_path / "pack.toml").write_text(PACK_TOML)
    (tmp_path / "mods").mkdir()
    return tmp_path


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "bot.db")
    insert_proposal(c, message_id=1, channel_id=100, mod_url="url", slug="sodium", project_id="AANobbMI", proposer_id=42, proposer_name="Alice")
    transition_to_merging(c, 1)
    yield c
    c.close()


# --- _build_commit_body ---

def test_build_commit_body_no_conflicts():
    proposal = _proposal()
    body = _build_commit_body(proposal, "Bob", 99, [], [])
    assert "Alice" in body
    assert "Bob" in body
    assert "Hard conflicts: none" in body
    assert "Soft conflicts: none" in body


def test_build_commit_body_with_conflicts():
    proposal = _proposal()
    soft = [SoftConflict(with_slug="iris", severity="medium", reason="shader")]
    body = _build_commit_body(proposal, "Bob", 99, ["incompatible with foo"], soft)
    assert "incompatible with foo" in body
    assert "iris (medium)" in body


# --- execute_merge_add ---

@pytest.mark.asyncio
async def test_execute_merge_add_success(pack_dir, conn):
    with (
        patch("discmod.merge.run_packwiz_refresh"),
        patch("discmod.merge.commit_and_push", return_value="deadbeef"),
    ):
        rv, sha = await execute_merge_add(
            _proposal(), "Bob", 99, pack_dir, _modrinth(), conn,
            "bot", "bot@x", "origin", "dev", False,
        )
    assert sha == "deadbeef"
    assert rv.project_id == "AANobbMI"


@pytest.mark.asyncio
async def test_execute_merge_add_blocked_on_hard_conflict(pack_dir, conn):
    dep = DependencyRef(project_id="existing-id", version_id=None, dependency_type="incompatible")
    resolved = _resolved(deps=[dep])

    import tomli_w
    (pack_dir / "mods" / "existing.pw.toml").write_bytes(
        tomli_w.dumps({
            "name": "Existing", "filename": "existing.jar", "side": "both",
            "download": {"url": "https://example.com/x.jar", "hash-format": "sha512", "hash": "a" * 128},
            "update": {"modrinth": {"mod-id": "existing-id", "version": "v1"}},
        }).encode()
    )

    modrinth = _modrinth(resolved=resolved)
    with pytest.raises(MergeBlocked):
        await execute_merge_add(
            _proposal(), "Bob", 99, pack_dir, modrinth, conn,
            "bot", "bot@x", "origin", "dev", True,
        )


@pytest.mark.asyncio
async def test_execute_merge_add_packwiz_failure_rolls_back(pack_dir, conn):
    from discmod.packwiz import PackwizError

    with (
        patch("discmod.merge.run_packwiz_refresh", side_effect=PackwizError("refresh failed")),
    ):
        with pytest.raises(MergeFailed, match="refresh failed"):
            await execute_merge_add(
                _proposal(), "Bob", 99, pack_dir, _modrinth(), conn,
                "bot", "bot@x", "origin", "dev", False,
            )

    assert not (pack_dir / "mods" / "sodium.pw.toml").exists()


@pytest.mark.asyncio
async def test_execute_merge_add_push_fails_raises_merge_push_failed(pack_dir, conn):
    with (
        patch("discmod.merge.run_packwiz_refresh"),
        patch("discmod.merge.commit_and_push", side_effect=Exception("push error")),
    ):
        with pytest.raises(MergePushFailed, match="push error"):
            await execute_merge_add(
                _proposal(), "Bob", 99, pack_dir, _modrinth(), conn,
                "bot", "bot@x", "origin", "dev", False,
            )


@pytest.mark.asyncio
async def test_execute_merge_add_with_soft_conflicts(pack_dir, conn):
    soft = [SoftConflict(with_slug="iris", severity="low", reason="overlap")]
    with (
        patch("discmod.merge.run_packwiz_refresh"),
        patch("discmod.merge.commit_and_push", return_value="sha") as mock_commit,
    ):
        rv, sha = await execute_merge_add(
            _proposal(), "Bob", 99, pack_dir, _modrinth(), conn,
            "bot", "bot@x", "origin", "dev", False, soft_conflicts=soft,
        )
    commit_body_arg = mock_commit.call_args[0][2]
    assert "iris (low)" in commit_body_arg


# --- execute_merge_remove ---

@pytest.fixture
def conn_remove(tmp_path):
    c = open_db(tmp_path / "bot.db")
    insert_proposal(c, message_id=2, channel_id=100, mod_url="REMOVE:sodium", slug="sodium",
                    project_id="AANobbMI", proposer_id=42, proposer_name="Alice")
    transition_to_merging(c, 2)
    yield c
    c.close()


def _remove_proposal():
    return {"message_id": 2, "slug": "sodium", "proposer_name": "Alice", "proposer_id": 42}


@pytest.mark.asyncio
async def test_execute_merge_remove_success(pack_dir, conn_remove):
    import tomli_w
    (pack_dir / "mods" / "sodium.pw.toml").write_bytes(
        tomli_w.dumps({
            "name": "Sodium", "filename": "sodium.jar", "side": "client",
            "download": {"url": "https://x.com/s.jar", "hash-format": "sha512", "hash": "a" * 128},
            "update": {"modrinth": {"mod-id": "AANobbMI", "version": "v1"}},
        }).encode()
    )
    with (
        patch("discmod.merge.run_packwiz_refresh"),
        patch("discmod.merge.commit_and_push", return_value="sha123"),
    ):
        sha = await execute_merge_remove(
            _remove_proposal(), "Bob", 99, pack_dir, conn_remove,
            "bot", "bot@x", "origin", "dev",
        )
    assert sha == "sha123"
    assert not (pack_dir / "mods" / "sodium.pw.toml").exists()


@pytest.mark.asyncio
async def test_execute_merge_remove_mod_not_found(pack_dir, conn_remove):
    with pytest.raises(MergeFailed, match="not found in pack"):
        await execute_merge_remove(
            _remove_proposal(), "Bob", 99, pack_dir, conn_remove,
            "bot", "bot@x", "origin", "dev",
        )


@pytest.mark.asyncio
async def test_execute_merge_remove_packwiz_failure(pack_dir, conn_remove):
    import tomli_w
    from discmod.packwiz import PackwizError

    (pack_dir / "mods" / "sodium.pw.toml").write_bytes(b"x")
    with patch("discmod.merge.run_packwiz_refresh", side_effect=PackwizError("fail")):
        with pytest.raises(MergeFailed):
            await execute_merge_remove(
                _remove_proposal(), "Bob", 99, pack_dir, conn_remove,
                "bot", "bot@x", "origin", "dev",
            )


@pytest.mark.asyncio
async def test_execute_merge_remove_push_fails(pack_dir, conn_remove):
    import tomli_w

    (pack_dir / "mods" / "sodium.pw.toml").write_bytes(b"x")
    with (
        patch("discmod.merge.run_packwiz_refresh"),
        patch("discmod.merge.commit_and_push", side_effect=Exception("push fail")),
    ):
        with pytest.raises(MergePushFailed):
            await execute_merge_remove(
                _remove_proposal(), "Bob", 99, pack_dir, conn_remove,
                "bot", "bot@x", "origin", "dev",
            )
