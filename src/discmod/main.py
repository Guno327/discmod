import asyncio
import logging
import subprocess
import sys

import anthropic
import discord
from discord import app_commands

from .commands.pack import setup_pack_commands
from .commands.propose import setup_propose_command
from .commands.reactions import setup_reaction_handler
from .config import load_config
from .db import fail_merging_proposals, open_db
from .git_ops import is_git_repo, pull_ff
from .modrinth import ModrinthClient
from .packwiz import read_pack_config


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


async def main() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)
    logger = logging.getLogger(__name__)

    # Verify pack dir
    if not cfg.pack_dir.exists():
        logger.error("PACK_DIR %s does not exist", cfg.pack_dir)
        sys.exit(1)
    if not is_git_repo(cfg.pack_dir):
        logger.error("PACK_DIR %s is not a git repository", cfg.pack_dir)
        sys.exit(1)
    if not (cfg.pack_dir / "pack.toml").exists():
        logger.error("PACK_DIR %s has no pack.toml", cfg.pack_dir)
        sys.exit(1)

    pack = read_pack_config(cfg.pack_dir)
    logger.info("Pack: MC %s / %s %s", pack.mc_version, pack.loader, pack.loader_version or "")

    # Verify packwiz binary
    result = subprocess.run(["packwiz", "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("packwiz not found on PATH")
        sys.exit(1)
    logger.info("packwiz: %s", result.stdout.strip())

    # Git pull
    pull_ff(cfg.pack_dir, cfg.git_remote, cfg.git_branch)

    # Open DB
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_db(cfg.db_path)

    # Reconcile mid-merge proposals
    stuck = fail_merging_proposals(conn)
    if stuck:
        logger.warning("Marked %d mid-merge proposals as failed: %s", len(stuck), stuck)

    # Modrinth client
    modrinth = ModrinthClient(cfg.modrinth_user_agent)
    await modrinth.smoke_check()
    logger.info("Modrinth API reachable")

    # Anthropic client (only when soft conflict checking is enabled)
    llm_client = (
        anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        if cfg.soft_conflicts_enabled
        else None
    )

    # Discord bot
    intents = discord.Intents.default()
    intents.reactions = True
    intents.members = True
    bot = discord.Client(intents=intents)
    tree = app_commands.CommandTree(bot)
    guild = discord.Object(id=cfg.discord_guild_id)

    setup_propose_command(
        tree, guild,
        proposal_channel_id=cfg.discord_proposal_channel_id,
        pack_dir=cfg.pack_dir,
        modrinth=modrinth,
        llm_client=llm_client,
        llm_model=cfg.llm_model,
        soft_conflicts_enabled=cfg.soft_conflicts_enabled,
        conn=conn,
        bot=bot,
        min_approvals=cfg.min_approvals,
        git_name=cfg.bot_git_name,
        git_email=cfg.bot_git_email,
        git_remote=cfg.git_remote,
        git_branch=cfg.git_branch,
        block_on_hard=cfg.block_on_hard_conflicts,
    )
    setup_pack_commands(
        tree, guild,
        pack_dir=cfg.pack_dir,
        conn=conn,
        modrinth=modrinth,
        admin_role_id=cfg.discord_admin_role_id,
        git_name=cfg.bot_git_name,
        git_email=cfg.bot_git_email,
        remote=cfg.git_remote,
        branch=cfg.git_branch,
    )
    setup_reaction_handler(
        bot, conn,
        pack_dir=cfg.pack_dir,
        modrinth=modrinth,
        min_approvals=cfg.min_approvals,
        admin_role_id=cfg.discord_admin_role_id,
        git_name=cfg.bot_git_name,
        git_email=cfg.bot_git_email,
        remote=cfg.git_remote,
        branch=cfg.git_branch,
        block_on_hard=cfg.block_on_hard_conflicts,
    )

    @bot.event
    async def on_ready() -> None:
        await tree.sync(guild=guild)
        logger.info("Logged in as %s, commands synced to guild %d", bot.user, cfg.discord_guild_id)

    try:
        await bot.start(cfg.discord_token)
    finally:
        await modrinth.close()
        conn.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
