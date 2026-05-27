"""
discmod-release: merge dev→main, tag, export .mrpack, publish GitHub release.

Required env vars:
  PACK_DIR        absolute path to packwiz git repo
  GITHUB_TOKEN    personal access token with repo scope

Optional env vars (same defaults as the bot):
  GIT_REMOTE          default: origin
  GIT_BRANCH          dev branch to merge from, default: dev
  GIT_MAIN_BRANCH     branch to merge into, default: main
  BOT_GIT_NAME        default: discmod-bot
  BOT_GIT_EMAIL       default: discmod@localhost
"""

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import httpx


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"ERROR: required env var {name!r} is missing", file=sys.stderr)
        sys.exit(1)
    return val


def _optional(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: {' '.join(args)}\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result


def _parse_github_remote(pack_dir: Path, remote: str) -> tuple[str, str]:
    """Return (owner, repo) parsed from the git remote URL."""
    result = _run(["git", "remote", "get-url", remote], cwd=pack_dir)
    url = result.stdout.strip()
    # SSH:   git@github.com:owner/repo.git
    # HTTPS: https://github.com/owner/repo.git
    match = re.search(r"github\.com[:/](.+?)/(.+?)(?:\.git)?$", url)
    if not match:
        print(f"ERROR: cannot parse GitHub owner/repo from remote URL: {url}", file=sys.stderr)
        sys.exit(1)
    return match.group(1), match.group(2)


def _read_pack_version(pack_dir: Path) -> str:
    data = tomllib.loads((pack_dir / "pack.toml").read_text())
    return data["version"]


def _read_pack_name(pack_dir: Path) -> str:
    data = tomllib.loads((pack_dir / "pack.toml").read_text())
    return data.get("name", "modpack")


def release() -> None:
    pack_dir = Path(_require("PACK_DIR"))
    github_token = _require("GITHUB_TOKEN")
    remote = _optional("GIT_REMOTE", "origin")
    dev_branch = _optional("GIT_BRANCH", "dev")
    main_branch = _optional("GIT_MAIN_BRANCH", "main")
    git_name = _optional("BOT_GIT_NAME", "discmod-bot")
    git_email = _optional("BOT_GIT_EMAIL", "discmod@localhost")

    if not (pack_dir / "pack.toml").exists():
        print(f"ERROR: no pack.toml in {pack_dir}", file=sys.stderr)
        sys.exit(1)

    version = _read_pack_version(pack_dir)
    pack_name = _read_pack_name(pack_dir)
    tag = f"v{version}"
    print(f"Releasing {pack_name} {version} (tag {tag})")

    owner, repo = _parse_github_remote(pack_dir, remote)
    print(f"GitHub repo: {owner}/{repo}")

    # Check tag doesn't already exist remotely
    result = subprocess.run(
        ["git", "ls-remote", "--tags", remote, tag],
        cwd=pack_dir, capture_output=True, text=True,
    )
    if result.stdout.strip():
        print(f"ERROR: tag {tag} already exists on remote. Bump version in pack.toml first.", file=sys.stderr)
        sys.exit(1)

    # Fetch and merge dev → main
    print(f"Fetching {remote}…")
    _run(["git", "fetch", remote], cwd=pack_dir)

    print(f"Checking out {main_branch}…")
    _run(["git", "checkout", main_branch], cwd=pack_dir)
    _run(["git", "reset", "--hard", f"{remote}/{main_branch}"], cwd=pack_dir)

    print(f"Merging {dev_branch} → {main_branch}…")
    author_args = ["-c", f"user.name={git_name}", "-c", f"user.email={git_email}"]
    _run(
        ["git"] + author_args + ["merge", "--no-ff", f"{remote}/{dev_branch}", "-m", f"Release {tag}"],
        cwd=pack_dir,
    )

    # Tag
    print(f"Tagging {tag}…")
    _run(["git"] + author_args + ["tag", tag], cwd=pack_dir)

    # Push main and tag
    print(f"Pushing {main_branch} and {tag}…")
    _run(["git", "push", remote, main_branch], cwd=pack_dir)
    _run(["git", "push", remote, tag], cwd=pack_dir)

    # Export .mrpack
    print("Running packwiz modrinth export…")
    mrpack_path = pack_dir / f"{pack_name}-{version}.mrpack"
    result = subprocess.run(
        ["packwiz", "modrinth", "export", "-o", str(mrpack_path)],
        cwd=pack_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: packwiz export failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    if not mrpack_path.exists():
        # Fall back to whatever packwiz produced
        candidates = sorted(pack_dir.glob("*.mrpack"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            print("ERROR: no .mrpack found after export", file=sys.stderr)
            sys.exit(1)
        mrpack_path = candidates[0]
    print(f"Exported: {mrpack_path.name} ({mrpack_path.stat().st_size // 1024} KB)")

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Create GitHub release
    print("Creating GitHub release…")
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"https://api.github.com/repos/{owner}/{repo}/releases",
            headers=headers,
            json={
                "tag_name": tag,
                "name": f"{pack_name} {version}",
                "body": f"Modpack release {version}",
                "draft": False,
                "prerelease": False,
            },
        )
        if resp.status_code not in (200, 201):
            print(f"ERROR: GitHub API {resp.status_code}: {resp.text}", file=sys.stderr)
            sys.exit(1)
        release_data = resp.json()
        upload_url = release_data["upload_url"].split("{")[0]  # strip {?name,label} template
        release_url = release_data["html_url"]

        # Upload .mrpack asset
        print(f"Uploading {mrpack_path.name}…")
        with mrpack_path.open("rb") as f:
            asset_resp = client.post(
                upload_url,
                headers={**headers, "Content-Type": "application/zip"},
                params={"name": mrpack_path.name},
                content=f.read(),
                timeout=120,
            )
        if asset_resp.status_code not in (200, 201):
            print(f"ERROR: asset upload {asset_resp.status_code}: {asset_resp.text}", file=sys.stderr)
            sys.exit(1)

    print(f"Done! Release: {release_url}")


if __name__ == "__main__":
    release()
