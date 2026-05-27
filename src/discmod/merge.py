import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .conflicts import check_hard_conflicts
from .db import update_proposal_status
from .git_ops import commit_and_push
from .models import PackMod, ResolvedVersion, SoftConflict
from .modrinth import ModrinthClient
from .packwiz import (
    PackwizError,
    read_current_pack,
    read_pack_config,
    run_packwiz_refresh,
    write_mod_entry,
)

logger = logging.getLogger(__name__)


class MergeBlocked(Exception):
    pass


class MergeFailed(Exception):
    pass


class MergePushFailed(Exception):
    pass


def _build_commit_body(
    proposal,
    approver_name: str,
    approver_id: int,
    hard: list[str],
    soft: list[SoftConflict],
) -> str:
    lines = [
        f"Proposed by {proposal['proposer_name']} (discord id {proposal['proposer_id']})",
        f"Approved by {approver_name} (discord id {approver_id})",
        "",
        f"Hard conflicts: {', '.join(hard) if hard else 'none'}",
        f"Soft conflicts: {', '.join(f'{s.with_slug} ({s.severity})' for s in soft) if soft else 'none'}",
    ]
    return "\n".join(lines)


async def execute_merge_add(
    proposal,
    approver_name: str,
    approver_id: int,
    pack_dir: Path,
    modrinth: ModrinthClient,
    conn: sqlite3.Connection,
    git_name: str,
    git_email: str,
    remote: str,
    branch: str,
    block_on_hard: bool,
    soft_conflicts: list[SoftConflict] | None = None,
) -> tuple[ResolvedVersion, str]:
    pack = read_pack_config(pack_dir)
    project = await modrinth.fetch_project(proposal["slug"])
    resolved = await modrinth.resolve_version(proposal["slug"], pack)

    current = read_current_pack(pack_dir)
    hard = await check_hard_conflicts(resolved, current)

    if hard and block_on_hard:
        raise MergeBlocked(f"Hard conflicts: {'; '.join(hard)}")

    entry_path = write_mod_entry(proposal["slug"], project, resolved, pack_dir)

    try:
        run_packwiz_refresh(pack_dir)
    except PackwizError as exc:
        entry_path.unlink(missing_ok=True)
        raise MergeFailed(str(exc)) from exc

    verb = "Add"
    message = f"{verb} {proposal['slug']} {resolved.version_number}"
    body = _build_commit_body(proposal, approver_name, approver_id, hard, soft_conflicts or [])

    try:
        sha = commit_and_push(pack_dir, message, body, git_name, git_email, remote, branch)
    except Exception as exc:
        raise MergePushFailed(str(exc)) from exc

    update_proposal_status(
        conn,
        proposal["message_id"],
        status="merged",
        resolved_version=resolved.version_number,
    )
    return resolved, sha


async def execute_merge_remove(
    proposal,
    approver_name: str,
    approver_id: int,
    pack_dir: Path,
    conn: sqlite3.Connection,
    git_name: str,
    git_email: str,
    remote: str,
    branch: str,
) -> str:
    slug = proposal["slug"]
    entry_path = pack_dir / "mods" / f"{slug}.pw.toml"
    if not entry_path.exists():
        raise MergeFailed(f"Mod {slug!r} not found in pack")

    entry_path.unlink()

    try:
        run_packwiz_refresh(pack_dir)
    except PackwizError as exc:
        raise MergeFailed(str(exc)) from exc

    body = _build_commit_body(proposal, approver_name, approver_id, [], [])
    message = f"Remove {slug}"

    try:
        sha = commit_and_push(pack_dir, message, body, git_name, git_email, remote, branch)
    except Exception as exc:
        raise MergePushFailed(str(exc)) from exc

    update_proposal_status(conn, proposal["message_id"], status="merged")
    return sha
