from .models import PackMod, ResolvedVersion


async def check_hard_conflicts(
    resolved: ResolvedVersion,
    current_pack: list[PackMod],
    modrinth_client=None,
) -> list[str]:
    """
    Returns human-readable conflict strings.
    Forward check: resolved's deps vs current pack.
    Reverse check: current pack mods' stored deps vs resolved's project_id.
    modrinth_client is accepted for future dep-fetching but not required for
    checks that use only declared dependency data already in resolved/current.
    """
    issues: list[str] = []
    current_by_id = {m.project_id: m for m in current_pack if m.project_id}
    current_ids = set(current_by_id)

    # Forward: walk new mod's declared deps
    for dep in resolved.dependencies:
        pid = dep.project_id
        if pid is None:
            continue
        if dep.dependency_type == "incompatible":
            if pid in current_ids:
                slug = current_by_id[pid].slug
                issues.append(f"{resolved.project_id} declares incompatible with {slug} (already in pack)")
        elif dep.dependency_type == "required":
            if pid not in current_ids:
                issues.append(f"requires {pid} (not in pack)")

    # Reverse: check existing mods' stored deps against the new mod
    new_pid = resolved.project_id
    for mod in current_pack:
        # We don't store dep data in PackMod (it's runtime-only from ResolvedVersion),
        # so reverse check is deferred; callers that have full version data can extend this.
        pass

    return issues


def check_reverse_conflicts(
    new_resolved: ResolvedVersion,
    existing_resolved_versions: list[ResolvedVersion],
) -> list[str]:
    """
    Full reverse check when caller has existing ResolvedVersion objects.
    Checks if any existing mod declares the new mod as incompatible.
    """
    issues: list[str] = []
    new_pid = new_resolved.project_id
    for existing in existing_resolved_versions:
        for dep in existing.dependencies:
            if dep.dependency_type == "incompatible" and dep.project_id == new_pid:
                issues.append(
                    f"{existing.project_id} declares incompatible with {new_pid} (new mod)"
                )
    return issues
