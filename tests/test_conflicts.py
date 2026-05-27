import pytest

from discmod.conflicts import check_hard_conflicts, check_reverse_conflicts
from discmod.models import DependencyRef, PackMod, ResolvedVersion


def make_resolved(project_id: str, deps: list[tuple[str, str]]) -> ResolvedVersion:
    return ResolvedVersion(
        project_id=project_id,
        version_id=f"vid-{project_id}",
        version_number="1.0.0",
        filename=f"{project_id}.jar",
        download_url=f"https://example.com/{project_id}.jar",
        sha512="a" * 128,
        sha1="b" * 40,
        file_size=1000,
        dependencies=tuple(
            DependencyRef(project_id=pid, version_id=None, dependency_type=dtype)
            for pid, dtype in deps
        ),
        client_side="optional",
        server_side="optional",
    )


def make_pack_mod(slug: str, project_id: str) -> PackMod:
    return PackMod(
        slug=slug,
        title=slug,
        description="",
        project_id=project_id,
        version_id=f"vid-{project_id}",
        version_number="1.0.0",
    )


@pytest.mark.asyncio
async def test_no_conflicts():
    resolved = make_resolved("new-mod", [])
    pack = [make_pack_mod("existing", "existing-id")]
    issues = await check_hard_conflicts(resolved, pack)
    assert issues == []


@pytest.mark.asyncio
async def test_forward_incompatible():
    resolved = make_resolved("new-mod", [("sodium-id", "incompatible")])
    pack = [make_pack_mod("sodium", "sodium-id")]
    issues = await check_hard_conflicts(resolved, pack)
    assert len(issues) == 1
    assert "incompatible" in issues[0]
    assert "sodium" in issues[0]


@pytest.mark.asyncio
async def test_forward_missing_required_dep():
    resolved = make_resolved("new-mod", [("fabric-api-id", "required")])
    pack = []  # fabric-api not in pack
    issues = await check_hard_conflicts(resolved, pack)
    assert len(issues) == 1
    assert "requires" in issues[0]
    assert "fabric-api-id" in issues[0]


@pytest.mark.asyncio
async def test_forward_required_dep_present():
    resolved = make_resolved("new-mod", [("fabric-api-id", "required")])
    pack = [make_pack_mod("fabric-api", "fabric-api-id")]
    issues = await check_hard_conflicts(resolved, pack)
    assert issues == []


@pytest.mark.asyncio
async def test_optional_dep_no_conflict():
    resolved = make_resolved("new-mod", [("optional-mod-id", "optional")])
    pack = []
    issues = await check_hard_conflicts(resolved, pack)
    assert issues == []


def test_reverse_incompatible():
    new_resolved = make_resolved("new-mod", [])
    existing = make_resolved("old-mod", [("new-mod", "incompatible")])
    issues = check_reverse_conflicts(new_resolved, [existing])
    assert len(issues) == 1
    assert "old-mod" in issues[0]
    assert "new-mod" in issues[0]


def test_reverse_no_conflict():
    new_resolved = make_resolved("new-mod", [])
    existing = make_resolved("old-mod", [("other-mod", "incompatible")])
    issues = check_reverse_conflicts(new_resolved, [existing])
    assert issues == []
