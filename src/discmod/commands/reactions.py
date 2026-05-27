import logging
import sqlite3
from pathlib import Path

import discord

from ..db import (
    count_approvals,
    get_proposal,
    insert_approval,
    transition_to_merging,
    update_proposal_status,
)
from ..merge import MergeBlocked, MergeFailed, MergePushFailed, execute_merge_add, execute_merge_remove
from ..modrinth import ModrinthClient

logger = logging.getLogger(__name__)


async def _post_result(
    bot: discord.Client,
    proposal,
    content: str,
) -> None:
    try:
        channel = bot.get_channel(proposal["channel_id"])
        if channel:
            await channel.send(content)
        if proposal["thread_id"]:
            thread = bot.get_channel(proposal["thread_id"])
            if thread:
                await thread.send(content)
    except Exception as exc:
        logger.error("Failed to post merge result: %s", exc)


async def run_merge(
    bot: discord.Client,
    conn: sqlite3.Connection,
    pack_dir: Path,
    modrinth: ModrinthClient,
    git_name: str,
    git_email: str,
    remote: str,
    branch: str,
    block_on_hard: bool,
    proposal: dict,
    approver_name: str,
    approver_id: int,
) -> None:
    """Execute the merge flow and post the result. Expects status already set to 'merging'."""
    is_remove = proposal["mod_url"].startswith("REMOVE:")
    try:
        if is_remove:
            sha = await execute_merge_remove(
                proposal, approver_name, approver_id,
                pack_dir, conn, git_name, git_email, remote, branch,
            )
            msg = f"✅ Removed **{proposal['slug']}** — commit `{sha[:8]}`"
        else:
            resolved, sha = await execute_merge_add(
                proposal, approver_name, approver_id,
                pack_dir, modrinth, conn,
                git_name, git_email, remote, branch,
                block_on_hard,
            )
            msg = (
                f"✅ Added **{proposal['slug']}** `{resolved.version_number}` "
                f"— commit `{sha[:8]}`"
            )
        await _post_result(bot, proposal, msg)

    except MergeBlocked as exc:
        update_proposal_status(conn, proposal["message_id"], "failed", error=str(exc))
        await _post_result(
            bot, proposal,
            f"⛔ Merge blocked for **{proposal['slug']}**: {exc}"
        )
    except (MergeFailed, MergePushFailed) as exc:
        update_proposal_status(conn, proposal["message_id"], "failed", error=str(exc))
        await _post_result(
            bot, proposal,
            f"❌ Merge failed for **{proposal['slug']}**: {exc}"
        )
    except Exception as exc:
        logger.error("Unexpected merge error for %s: %s", proposal["slug"], exc, exc_info=True)
        update_proposal_status(conn, proposal["message_id"], "failed", error=str(exc))
        await _post_result(
            bot, proposal,
            f"❌ Unexpected error merging **{proposal['slug']}**: {exc}"
        )


def setup_reaction_handler(
    bot: discord.Client,
    conn: sqlite3.Connection,
    pack_dir: Path,
    modrinth: ModrinthClient,
    min_approvals: int,
    admin_role_id: int | None,
    git_name: str,
    git_email: str,
    remote: str,
    branch: str,
    block_on_hard: bool,
) -> None:

    @bot.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == bot.user.id:
            return

        proposal = get_proposal(conn, payload.message_id)
        if not proposal:
            return
        if proposal["status"] != "pending":
            return

        emoji = str(payload.emoji)
        member = payload.member

        is_admin = (
            admin_role_id is not None
            and member is not None
            and any(r.id == admin_role_id for r in member.roles)
        )

        if emoji == "❌":
            is_proposer = payload.user_id == proposal["proposer_id"]
            if is_proposer or is_admin:
                update_proposal_status(conn, payload.message_id, "rejected")
                await _post_result(
                    bot, proposal,
                    f"❌ Proposal for **{proposal['slug']}** rejected by <@{payload.user_id}>."
                )
            return

        if emoji != "✅":
            return

        # Prevent proposer from self-approving
        if payload.user_id == proposal["proposer_id"]:
            try:
                channel = bot.get_channel(payload.channel_id)
                if channel:
                    msg = await channel.fetch_message(payload.message_id)
                    await msg.remove_reaction(payload.emoji, discord.Object(id=payload.user_id))
            except Exception:
                pass
            return

        user_name = str(member) if member else str(payload.user_id)
        insert_approval(conn, payload.message_id, payload.user_id, user_name)

        approvals = count_approvals(conn, payload.message_id, exclude_user_id=proposal["proposer_id"])
        if approvals < min_approvals:
            return

        if not transition_to_merging(conn, payload.message_id):
            return  # another handler beat us

        await run_merge(
            bot, conn, pack_dir, modrinth,
            git_name, git_email, remote, branch, block_on_hard,
            proposal, user_name, payload.user_id,
        )
