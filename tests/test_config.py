import os
from unittest.mock import patch

import pytest

from discmod.config import load_config

_BASE = {
    "DISCORD_TOKEN": "tok",
    "DISCORD_GUILD_ID": "123",
    "DISCORD_PROPOSAL_CHANNEL_ID": "456",
    "MODRINTH_USER_AGENT": "test/test/0.1",
    "SOFT_CONFLICTS_ENABLED": "false",
}


def _env(tmp_path, **overrides):
    return {**_BASE, "PACK_DIR": str(tmp_path), **overrides}


def test_minimal_config(tmp_path):
    with patch.dict(os.environ, _env(tmp_path), clear=True):
        cfg = load_config()
    assert cfg.discord_token == "tok"
    assert cfg.discord_guild_id == 123
    assert cfg.discord_proposal_channel_id == 456
    assert cfg.pack_dir == tmp_path
    assert cfg.db_path == tmp_path.parent / "bot.db"
    assert cfg.git_remote == "origin"
    assert cfg.git_branch == "dev"
    assert cfg.min_approvals == 1
    assert not cfg.block_on_hard_conflicts
    assert cfg.pr_on_hard_conflicts
    assert not cfg.soft_conflicts_enabled
    assert cfg.discord_admin_role_id is None
    assert cfg.anthropic_api_key is None


def test_missing_required_var_raises():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="Required env var"):
            load_config()


def test_explicit_db_path(tmp_path):
    db = tmp_path / "custom.db"
    with patch.dict(os.environ, _env(tmp_path, DB_PATH=str(db)), clear=True):
        cfg = load_config()
    assert cfg.db_path == db


def test_soft_conflicts_enabled_without_api_key_raises(tmp_path):
    with patch.dict(os.environ, _env(tmp_path, SOFT_CONFLICTS_ENABLED="true"), clear=True):
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            load_config()


def test_soft_conflicts_enabled_with_api_key(tmp_path):
    with patch.dict(
        os.environ,
        _env(tmp_path, SOFT_CONFLICTS_ENABLED="true", ANTHROPIC_API_KEY="sk-test"),
        clear=True,
    ):
        cfg = load_config()
    assert cfg.soft_conflicts_enabled
    assert cfg.anthropic_api_key == "sk-test"


def test_admin_role_id(tmp_path):
    with patch.dict(os.environ, _env(tmp_path, DISCORD_ADMIN_ROLE_ID="789"), clear=True):
        cfg = load_config()
    assert cfg.discord_admin_role_id == 789


def test_block_on_hard_conflicts_true(tmp_path):
    with patch.dict(os.environ, _env(tmp_path, BLOCK_ON_HARD_CONFLICTS="true"), clear=True):
        cfg = load_config()
    assert cfg.block_on_hard_conflicts


def test_pr_on_hard_conflicts_false(tmp_path):
    with patch.dict(os.environ, _env(tmp_path, PR_ON_HARD_CONFLICTS="false"), clear=True):
        cfg = load_config()
    assert not cfg.pr_on_hard_conflicts


def test_custom_optionals(tmp_path):
    extras = {
        "GIT_REMOTE": "upstream",
        "GIT_BRANCH": "main",
        "MIN_APPROVALS": "3",
        "LLM_MODEL": "claude-opus-4-7",
        "LOG_LEVEL": "DEBUG",
        "BOT_GIT_NAME": "mybot",
        "BOT_GIT_EMAIL": "mybot@example.com",
    }
    with patch.dict(os.environ, _env(tmp_path, **extras), clear=True):
        cfg = load_config()
    assert cfg.git_remote == "upstream"
    assert cfg.git_branch == "main"
    assert cfg.min_approvals == 3
    assert cfg.llm_model == "claude-opus-4-7"
    assert cfg.log_level == "DEBUG"
    assert cfg.bot_git_name == "mybot"
    assert cfg.bot_git_email == "mybot@example.com"
