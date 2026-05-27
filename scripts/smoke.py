#!/usr/bin/env python3
"""
Smoke test: resolve a mod, write its entry, run packwiz refresh — all in a tmpdir.
Requires network access. Run with:
    python scripts/smoke.py sodium 1.21.1 fabric
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import tomli_w
from discmod.modrinth import ModrinthClient
from discmod.models import PackConfig
from discmod.packwiz import write_mod_entry, run_packwiz_refresh, read_pack_config


async def smoke(slug: str, mc_version: str, loader: str) -> None:
    user_agent = "discmod-smoke/0.1.0 (smoke-test)"
    client = ModrinthClient(user_agent)

    pack_cfg = PackConfig(mc_version=mc_version, loader=loader, loader_version=None)

    print(f"Resolving {slug} for MC {mc_version} / {loader}...")
    project = await client.fetch_project(slug)
    resolved = await client.resolve_version(slug, pack_cfg)
    print(f"  -> {resolved.version_number} ({resolved.filename})")
    print(f"  -> deps: {resolved.dependencies}")

    with tempfile.TemporaryDirectory() as tmpdir:
        pack_dir = Path(tmpdir)
        (pack_dir / "mods").mkdir()

        pack_toml = {
            "name": "smoke-pack",
            "author": "smoke",
            "version": "0.1.0",
            "pack-format": "packwiz:1.1.0",
            "index": {"file": "index.toml", "hash-format": "sha256", "hash": ""},
            "versions": {
                "minecraft": mc_version,
                loader: "0.0.0",
            },
        }
        (pack_dir / "pack.toml").write_bytes(tomli_w.dumps(pack_toml).encode())
        (pack_dir / "index.toml").write_text('[files]\n')

        entry = write_mod_entry(slug, project, resolved, pack_dir)
        print(f"  -> wrote {entry}")

        try:
            run_packwiz_refresh(pack_dir)
            print("  -> packwiz refresh OK")
        except Exception as exc:
            print(f"  -> packwiz refresh FAILED (is packwiz installed?): {exc}")

    await client.close()
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python scripts/smoke.py <slug> <mc_version> <loader>")
        sys.exit(1)
    asyncio.run(smoke(sys.argv[1], sys.argv[2], sys.argv[3]))
