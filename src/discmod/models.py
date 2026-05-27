from dataclasses import dataclass, field


@dataclass(frozen=True)
class PackConfig:
    mc_version: str
    loader: str
    loader_version: str | None


@dataclass(frozen=True)
class DependencyRef:
    project_id: str | None
    version_id: str | None
    dependency_type: str  # "required" | "optional" | "incompatible" | "embedded"


@dataclass(frozen=True)
class ResolvedVersion:
    project_id: str
    version_id: str
    version_number: str
    filename: str
    download_url: str
    sha512: str
    sha1: str
    file_size: int
    dependencies: tuple[DependencyRef, ...]
    client_side: str
    server_side: str


@dataclass(frozen=True)
class PackMod:
    slug: str
    title: str
    description: str
    project_id: str
    version_id: str
    version_number: str


@dataclass
class SoftConflict:
    with_slug: str
    severity: str  # "low" | "medium" | "high"
    reason: str


@dataclass
class ConflictReport:
    hard: list[str] = field(default_factory=list)
    soft: list[SoftConflict] = field(default_factory=list)
    ai_summary: str = ""
