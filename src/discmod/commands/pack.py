import logging
import sqlite3
from pathlib import Path

import discord
from discord import app_commands

from ..db import get_pending_proposals, insert_proposal
from ..git_ops import get_last_commit
from ..modrinth import ModrinthClient
from ..packwiz import (
    PackwizError,
    read_current_pack,
    read_pack_config,
    run_packwiz_export,
    run_packwiz_refresh,
)

logger = logging.getLogger(__name__)

MAX_EMBED_FIELD = 1024
MAX_DISCORD_FILE = 25 * 1024 * 1024  # 25 MB


def _is_admin(interaction: discord.Interaction, admin_role_id: int | None) -> bool:
    if admin_role_id is None:
        return True  # no role configured → everyone is admin
    member = interaction.user
    if not hasattr(member, "roles"):
        return False
    return any(r.id == admin_role_id for r in member.roles)


def setup_pack_commands(
    tree: app_commands.CommandTree,
    guild: discord.Object,
    pack_dir: Path,
    conn: sqlite3.Connection,
    modrinth: ModrinthClient,
    admin_role_id: int | None,
    git_name: str,
    git_email: str,
    remote: str,
    branch: str,
) -> None:
    pack_group = app_commands.Group(name="pack", description="Pack management", guild_ids=[guild.id])

    @pack_group.command(name="status", description="Show pack info and last commit")
    async def status(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            pack = read_pack_config(pack_dir)
            mods = read_current_pack(pack_dir)
            commit = get_last_commit(pack_dir)
        except Exception as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        embed = discord.Embed(title="Pack Status", color=discord.Color.blurple())
        embed.add_field(name="MC Version", value=pack.mc_version, inline=True)
        embed.add_field(name="Loader", value=f"{pack.loader} {pack.loader_version or ''}", inline=True)
        embed.add_field(name="Mods", value=str(len(mods)), inline=True)
        if commit:
            embed.add_field(
                name="Last Commit",
                value=f"`{commit['sha'][:8]}` by {commit['author']}\n{commit['subject']}",
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @pack_group.command(name="list", description="List all mods in the pack")
    @app_commands.describe(search="Optional substring filter")
    async def list_mods(interaction: discord.Interaction, search: str = "") -> None:
        await interaction.response.defer(thinking=True)
        mods = read_current_pack(pack_dir)
        if search:
            mods = [m for m in mods if search.lower() in m.slug.lower() or search.lower() in m.title.lower()]

        if not mods:
            await interaction.followup.send("No mods found.", ephemeral=True)
            return

        PAGE = 20
        pages = [mods[i : i + PAGE] for i in range(0, len(mods), PAGE)]
        for i, page in enumerate(pages):
            lines = [f"• **{m.slug}** (`{m.version_number}`)" for m in page]
            embed = discord.Embed(
                title=f"Mods ({len(mods)} total)" + (f" — page {i+1}/{len(pages)}" if len(pages) > 1 else ""),
                description="\n".join(lines),
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed)

    @pack_group.command(name="remove", description="Propose removal of a mod from the pack")
    @app_commands.describe(slug="Mod slug to remove")
    async def remove(interaction: discord.Interaction, slug: str) -> None:
        mods = read_current_pack(pack_dir)
        mod_map = {m.slug: m for m in mods}
        if slug not in mod_map:
            await interaction.response.send_message(f"❌ Mod `{slug}` not in pack.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        mod = mod_map[slug]

        embed = discord.Embed(
            title=f"Removal Proposal: {mod.title or slug}",
            description=f"Proposing removal of **{slug}**",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Proposed by", value=interaction.user.mention, inline=True)
        embed.set_footer(text="React ✅ to approve, ❌ to reject")
        msg = await interaction.followup.send(embed=embed, wait=True)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        insert_proposal(
            conn,
            message_id=msg.id,
            channel_id=interaction.channel_id,
            mod_url=f"REMOVE:{slug}",
            slug=slug,
            project_id=mod.project_id,
            proposer_id=interaction.user.id,
            proposer_name=str(interaction.user),
        )
        logger.info("Removal proposal: %s by %s (msg %d)", slug, interaction.user, msg.id)

    @pack_group.command(name="rebuild", description="Run packwiz refresh and commit if changed (admin)")
    async def rebuild(interaction: discord.Interaction) -> None:
        if not _is_admin(interaction, admin_role_id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            run_packwiz_refresh(pack_dir)
        except PackwizError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        await interaction.followup.send("✅ packwiz refresh complete.")

    @pack_group.command(name="export", description="Export pack as .mrpack")
    async def export_pack(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            mrpack = run_packwiz_export(pack_dir)
        except PackwizError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        size = mrpack.stat().st_size
        if size <= MAX_DISCORD_FILE:
            await interaction.followup.send(
                "📦 Pack export:",
                file=discord.File(str(mrpack)),
            )
        else:
            await interaction.followup.send(
                f"📦 Export at `{mrpack}` ({size / 1024 / 1024:.1f} MB — too large to upload)"
            )

    @pack_group.command(name="pending", description="List pending proposals")
    async def pending(interaction: discord.Interaction) -> None:
        proposals = get_pending_proposals(conn)
        if not proposals:
            await interaction.response.send_message("No pending proposals.", ephemeral=True)
            return

        lines = []
        for p in proposals:
            jump = f"https://discord.com/channels/{interaction.guild_id}/{p['channel_id']}/{p['message_id']}"
            lines.append(f"• **{p['slug']}** — proposed by {p['proposer_name']} — [jump]({jump})")

        embed = discord.Embed(
            title=f"Pending Proposals ({len(proposals)})",
            description="\n".join(lines),
            color=discord.Color.yellow(),
        )
        await interaction.response.send_message(embed=embed)

    tree.add_command(pack_group)
