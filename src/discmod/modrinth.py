import asyncio
import json
import logging
import re
from typing import Any

import httpx

from .models import DependencyRef, PackConfig, ResolvedVersion

logger = logging.getLogger(__name__)

BASE_URL = "https://api.modrinth.com/v2"
_BARE_PROJECT_ID = re.compile(r"^[0-9A-Za-z]{8}$")


class ModrinthError(Exception):
    pass


class NoCompatibleVersion(ModrinthError):
    def __init__(self, slug: str, mc_version: str, loader: str) -> None:
        super().__init__(f"No compatible version of {slug!r} for MC {mc_version} / {loader}")
        self.slug = slug


def parse_slug(url_or_slug: str) -> str:
    """Extract slug/id from a Modrinth URL or return bare slug/id unchanged."""
    url_or_slug = url_or_slug.strip()
    match = re.search(r"modrinth\.com/(?:mod|plugin|datapack|shader|resourcepack|modpack)/([^/\s?#]+)", url_or_slug)
    if match:
        return match.group(1)
    if "/" not in url_or_slug and " " not in url_or_slug:
        return url_or_slug
    raise ModrinthError(f"Cannot parse Modrinth URL or slug: {url_or_slug!r}")


class ModrinthClient:
    def __init__(self, user_agent: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": user_agent},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._client.get(path, params=params)
                remaining = int(resp.headers.get("X-Ratelimit-Remaining", "100"))
                if remaining < 10:
                    logger.warning("Modrinth rate limit low: %d remaining", remaining)
                    await asyncio.sleep(2)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "5"))
                    logger.warning("Modrinth 429; sleeping %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    last_exc = ModrinthError(f"Rate limited (attempt {attempt+1})")
                    continue
                if resp.status_code >= 500:
                    wait = 2 ** attempt
                    logger.warning("Modrinth %d; retrying in %ds", resp.status_code, wait)
                    await asyncio.sleep(wait)
                    last_exc = ModrinthError(f"Server error {resp.status_code}")
                    continue
                resp.raise_for_status()
                logger.debug("GET %s -> %d", path, resp.status_code)
                return resp.json()
            except httpx.HTTPError as exc:
                wait = 2 ** attempt
                logger.warning("HTTP error on %s: %s; retrying in %ds", path, exc, wait)
                await asyncio.sleep(wait)
                last_exc = exc
        raise ModrinthError(f"Request failed after 3 attempts: {last_exc}") from last_exc

    async def fetch_project(self, slug_or_id: str) -> dict:
        return await self._get(f"/project/{slug_or_id}")

    async def fetch_versions(self, slug_or_id: str, mc_version: str, loader: str) -> list[dict]:
        return await self._get(
            f"/project/{slug_or_id}/version",
            game_versions=json.dumps([mc_version]),
            loaders=json.dumps([loader]),
        )

    async def resolve_version(self, slug: str, pack: PackConfig) -> ResolvedVersion:
        versions = await self.fetch_versions(slug, pack.mc_version, pack.loader)
        if not versions:
            raise NoCompatibleVersion(slug, pack.mc_version, pack.loader)

        # Prefer release > beta > alpha
        tier_order = {"release": 0, "beta": 1, "alpha": 2}
        best = min(versions, key=lambda v: tier_order.get(v.get("version_type", "alpha"), 3))

        primary = next((f for f in best["files"] if f.get("primary")), best["files"][0])
        deps = [
            DependencyRef(
                project_id=d.get("project_id"),
                version_id=d.get("version_id"),
                dependency_type=d["dependency_type"],
            )
            for d in best.get("dependencies", [])
        ]

        project = await self.fetch_project(slug)

        return ResolvedVersion(
            project_id=best["project_id"],
            version_id=best["id"],
            version_number=best["version_number"],
            filename=primary["filename"],
            download_url=primary["url"],
            sha512=primary["hashes"]["sha512"],
            sha1=primary["hashes"]["sha1"],
            file_size=primary["size"],
            dependencies=tuple(deps),
            client_side=project.get("client_side", "optional"),
            server_side=project.get("server_side", "optional"),
        )

    async def fetch_project_by_id_batch(self, project_ids: list[str]) -> dict[str, dict]:
        if not project_ids:
            return {}
        data = await self._get("/projects", ids=json.dumps(project_ids))
        return {p["id"]: p for p in data}

    async def smoke_check(self) -> None:
        """Verify API connectivity at startup."""
        await self._get("/")
