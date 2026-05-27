import subprocess
import tomllib
from pathlib import Path

import tomli_w

from .models import PackConfig, PackMod, ResolvedVersion


class PackwizError(Exception):
    pass


def read_pack_config(pack_dir: Path) -> PackConfig:
    data = tomllib.loads((pack_dir / "pack.toml").read_text())
    versions = data["versions"]
    mc_version = versions["minecraft"]
    loader = next(k for k in versions if k != "minecraft")
    loader_version = versions.get(loader)
    return PackConfig(mc_version=mc_version, loader=loader, loader_version=loader_version)


def read_current_pack(pack_dir: Path) -> list[PackMod]:
    mods_dir = pack_dir / "mods"
    if not mods_dir.exists():
        return []
    mods = []
    for path in mods_dir.glob("*.pw.toml"):
        data = tomllib.loads(path.read_text())
        slug = path.name.removesuffix(".pw.toml")
        modrinth = data.get("update", {}).get("modrinth", {})
        mods.append(PackMod(
            slug=slug,
            title=data.get("name", slug),
            description="",
            project_id=modrinth.get("mod-id", ""),
            version_id=modrinth.get("version", ""),
            version_number=data.get("filename", ""),
        ))
    return mods


def _side(client_side: str, server_side: str) -> str:
    if client_side == "required" and server_side == "unsupported":
        return "client"
    if client_side == "unsupported" and server_side == "required":
        return "server"
    return "both"


def write_mod_entry(
    slug: str,
    project: dict,
    resolved: ResolvedVersion,
    pack_dir: Path,
) -> Path:
    side = _side(resolved.client_side, resolved.server_side)
    entry = {
        "name": project.get("title", slug),
        "filename": resolved.filename,
        "side": side,
        "download": {
            "url": resolved.download_url,
            "hash-format": "sha512",
            "hash": resolved.sha512,
        },
        "update": {
            "modrinth": {
                "mod-id": resolved.project_id,
                "version": resolved.version_id,
            }
        },
    }
    path = pack_dir / "mods" / f"{slug}.pw.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tomli_w.dumps(entry).encode())
    return path


def run_packwiz_refresh(pack_dir: Path) -> None:
    result = subprocess.run(
        ["packwiz", "refresh"],
        cwd=pack_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PackwizError(f"packwiz refresh failed:\n{result.stderr}")


def run_packwiz_export(pack_dir: Path, output_path: Path | None = None) -> Path:
    cmd = ["packwiz", "modrinth", "export"]
    if output_path:
        cmd += ["-o", str(output_path)]
    result = subprocess.run(cmd, cwd=pack_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise PackwizError(f"packwiz modrinth export failed:\n{result.stderr}")
    if output_path:
        return output_path
    # packwiz names the file based on pack.toml name + version
    mrpacks = list(pack_dir.glob("*.mrpack"))
    if not mrpacks:
        raise PackwizError("Export succeeded but no .mrpack found")
    return max(mrpacks, key=lambda p: p.stat().st_mtime)
