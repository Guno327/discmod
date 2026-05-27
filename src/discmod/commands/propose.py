import logging
import sqlite3
from pathlib import Path

import discord
from discord import app_commands

from ..db import insert_proposal, get_proposal, transition_to_merging
from ..llm import soft_conflict_check
from ..models import PackMod, ResolvedVersion, SoftConflict
from ..modrinth import ModrinthClient, ModrinthError, NoCompatibleVersion, parse_slug
from ..packwiz import read_current_pack, read_pack_config

logger = logging.getLogger(__name__)


def _embed_color(hard: list[str], soft: list[SoftConflict]) -> discord.Color:
    if hard:
        return discord.Color.red()
    if soft:
        return discord.Color.orange()
    return discord.Color.green()


def build_proposal_embed(
    project: dict,
    resolved: ResolvedVersion,
    proposer: discord.User | discord.Member,
    hard: list[str],
    soft: list[SoftConflict],
    ai_summary: str,
    auto_merge: bool = False,
) -> discord.Embed:
    title = project.get("title", resolved.project_id)
    slug = project.get("slug", "")
    url = f"https://modrinth.com/mod/{slug}"

    embed = discord.Embed(
        title=f"Proposal: {title}",
        url=url,
        color=_embed_color(hard, soft),
    )
    if ai_summary:
        embed.description = ai_summary
    embed.add_field(name="Version", value=resolved.version_number, inline=True)
    embed.add_field(name="Proposed by", value=proposer.mention, inline=True)

    if hard:
        embed.add_field(
            name="⛔ Hard conflicts",
            value="\n".join(f"• {h}" for h in hard),
            inline=False,
        )
    if soft:
        embed.add_field(
            name="⚠️ Possible soft conflicts",
            value="\n".join(f"• {s.with_slug} ({s.severity}): {s.reason}" for s in soft),
            inline=False,
        )

    footer = "Auto-merging (MIN_APPROVALS=0)…" if auto_merge else "React ✅ to approve, ❌ to reject"
    embed.set_footer(text=footer)
    return embed


def setup_propose_command(
    tree: app_commands.CommandTree,
    guild: discord.Object,
    proposal_channel_id: int,
    pack_dir: Path,
    modrinth: ModrinthClient,
    llm_client,
    llm_model: str,
    soft_conflicts_enabled: bool,
    conn: sqlite3.Connection,
    bot: discord.Client | None = None,
    min_approvals: int = 1,
    git_name: str = "discmod-bot",
    git_email: str = "discmod@localhost",
    git_remote: str = "origin",
    git_branch: str = "dev",
    block_on_hard: bool = False,
) -> None:

    @tree.command(name="propose", description="Propose a mod for the pack", guild=guild)
    @app_commands.describe(slug="Modrinth mod slug or URL")
    async def propose(interaction: discord.Interaction, slug: str) -> None:
        if interaction.channel_id != proposal_channel_id:
            await interaction.response.send_message(
                f"Proposals must be made in <#{proposal_channel_id}>.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            slug = parse_slug(slug)
        except ModrinthError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        try:
            project = await modrinth.fetch_project(slug)
        except Exception as exc:
            await interaction.followup.send(f"❌ Couldn't find mod `{slug}`: {exc}", ephemeral=True)
            return

        try:
            pack = read_pack_config(pack_dir)
            resolved = await modrinth.resolve_version(slug, pack)
        except NoCompatibleVersion as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        except Exception as exc:
            await interaction.followup.send(f"❌ Version resolution failed: {exc}", ephemeral=True)
            return

        current = read_current_pack(pack_dir)

        from ..conflicts import check_hard_conflicts
        hard = await check_hard_conflicts(resolved, current)

        # Enrich current pack mods with descriptions for LLM
        new_mod = PackMod(
            slug=slug,
            title=project.get("title", slug),
            description=(project.get("description") or project.get("body", ""))[:300],
            project_id=resolved.project_id,
            version_id=resolved.version_id,
            version_number=resolved.version_number,
        )

        if soft_conflicts_enabled:
            ai_summary, soft = await soft_conflict_check(new_mod, current, llm_client, llm_model)
        else:
            ai_summary, soft = "", []

        auto_merge = min_approvals == 0
        embed = build_proposal_embed(project, resolved, interaction.user, hard, soft, ai_summary, auto_merge)
        msg = await interaction.followup.send(embed=embed, wait=True)

        if not auto_merge:
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")

        insert_proposal(
            conn,
            message_id=msg.id,
            channel_id=interaction.channel_id,
            mod_url=slug,
            slug=slug,
            project_id=resolved.project_id,
            proposer_id=interaction.user.id,
            proposer_name=str(interaction.user),
            ai_summary=ai_summary,
        )
        logger.info("Proposal created: %s by %s (msg %d)", slug, interaction.user, msg.id)

        if auto_merge:
            from .reactions import run_merge
            proposal = get_proposal(conn, msg.id)
            if transition_to_merging(conn, msg.id):
                await run_merge(
                    bot, conn, pack_dir, modrinth,
                    git_name, git_email, git_remote, git_branch, block_on_hard,
                    proposal, str(interaction.user), interaction.user.id,
                )
