import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _run(args: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, **kwargs)


def commit_and_push(
    pack_dir: Path,
    message: str,
    body: str,
    git_name: str,
    git_email: str,
    remote: str,
    branch: str,
) -> str:
    author_args = [f"-c", f"user.name={git_name}", f"-c", f"user.email={git_email}"]
    _run(["git", "add", "-A"], cwd=pack_dir)
    result = _run(
        ["git"] + author_args + ["commit", "-m", message, "-m", body],
        cwd=pack_dir,
    )
    sha = _get_head_sha(pack_dir)
    logger.info("Committed %s", sha)
    try:
        _run(["git", "push", remote, branch], cwd=pack_dir)
        logger.info("Pushed to %s/%s", remote, branch)
    except subprocess.CalledProcessError as exc:
        logger.error("Push failed (commit %s is local): %s", sha, exc.stderr)
        raise
    return sha


def _get_head_sha(pack_dir: Path) -> str:
    result = _run(["git", "rev-parse", "HEAD"], cwd=pack_dir)
    return result.stdout.strip()


def pull_ff(pack_dir: Path, remote: str, branch: str) -> None:
    try:
        _run(["git", "pull", "--ff-only", remote, branch], cwd=pack_dir)
    except subprocess.CalledProcessError as exc:
        logger.warning("git pull --ff-only failed (non-fast-forward?): %s", exc.stderr)


def is_git_repo(pack_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=pack_dir,
        capture_output=True,
    )
    return result.returncode == 0


def get_last_commit(pack_dir: Path) -> dict | None:
    try:
        result = _run(
            ["git", "log", "-1", "--format=%H%n%an%n%s"],
            cwd=pack_dir,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 3:
            return {"sha": lines[0], "author": lines[1], "subject": lines[2]}
    except subprocess.CalledProcessError:
        pass
    return None


def create_pr_branch(
    pack_dir: Path,
    branch_name: str,
    git_name: str,
    git_email: str,
    remote: str,
) -> None:
    _run(["git", "checkout", "-b", branch_name], cwd=pack_dir)
    _run(["git", "push", "-u", remote, branch_name], cwd=pack_dir)


def checkout_branch(pack_dir: Path, branch: str) -> None:
    _run(["git", "checkout", branch], cwd=pack_dir)


def get_remote_url(pack_dir: Path, remote: str) -> str | None:
    try:
        result = _run(["git", "remote", "get-url", remote], cwd=pack_dir)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def github_releases_url(remote_url: str) -> str | None:
    """Convert a git remote URL to a GitHub releases page URL, or None if not GitHub."""
    match = re.search(r"github\.com[:/](.+?)/(.+?)(?:\.git)?$", remote_url.strip())
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    return f"https://github.com/{owner}/{repo}/releases"
