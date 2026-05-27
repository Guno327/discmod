import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    discord_token: str
    discord_guild_id: int
    discord_proposal_channel_id: int
    pack_dir: Path
    anthropic_api_key: str | None
    modrinth_user_agent: str
    db_path: Path
    git_remote: str
    git_branch: str
    bot_git_name: str
    bot_git_email: str
    min_approvals: int
    block_on_hard_conflicts: bool
    pr_on_hard_conflicts: bool
    soft_conflicts_enabled: bool
    llm_model: str
    discord_admin_role_id: int | None
    log_level: str


def load_config() -> Config:
    def require(name: str) -> str:
        val = os.environ.get(name)
        if not val:
            raise RuntimeError(f"Required env var {name!r} is missing or empty")
        return val

    def optional(name: str, default: str) -> str:
        return os.environ.get(name) or default

    pack_dir = Path(require("PACK_DIR"))
    db_path_raw = os.environ.get("DB_PATH")
    db_path = Path(db_path_raw) if db_path_raw else pack_dir.parent / "bot.db"

    admin_role_raw = os.environ.get("DISCORD_ADMIN_ROLE_ID")
    admin_role = int(admin_role_raw) if admin_role_raw else None

    soft_conflicts_enabled = optional("SOFT_CONFLICTS_ENABLED", "true").lower() == "true"
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY") or None
    if soft_conflicts_enabled and not anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required when SOFT_CONFLICTS_ENABLED=true (the default). "
            "Either set the key or disable with SOFT_CONFLICTS_ENABLED=false."
        )

    return Config(
        discord_token=require("DISCORD_TOKEN"),
        discord_guild_id=int(require("DISCORD_GUILD_ID")),
        discord_proposal_channel_id=int(require("DISCORD_PROPOSAL_CHANNEL_ID")),
        pack_dir=pack_dir,
        anthropic_api_key=anthropic_api_key,
        modrinth_user_agent=require("MODRINTH_USER_AGENT"),
        db_path=db_path,
        git_remote=optional("GIT_REMOTE", "origin"),
        git_branch=optional("GIT_BRANCH", "dev"),
        bot_git_name=optional("BOT_GIT_NAME", "discmod-bot"),
        bot_git_email=optional("BOT_GIT_EMAIL", "discmod@localhost"),
        min_approvals=int(optional("MIN_APPROVALS", "1")),
        block_on_hard_conflicts=optional("BLOCK_ON_HARD_CONFLICTS", "false").lower() == "true",
        pr_on_hard_conflicts=optional("PR_ON_HARD_CONFLICTS", "true").lower() == "true",
        soft_conflicts_enabled=soft_conflicts_enabled,
        llm_model=optional("LLM_MODEL", "claude-haiku-4-5-20251001"),
        discord_admin_role_id=admin_role,
        log_level=optional("LOG_LEVEL", "INFO"),
    )
