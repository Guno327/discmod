# Collaborative Modpack Builder — Project Spec

## 1. Purpose & Scope

Build a Discord-bot-driven system that lets a small group collaboratively curate a Minecraft modpack via Modrinth. Mods are proposed in Discord, approved by reaction-based sign-off from at least one other person, then deterministically resolved and committed to a `packwiz`-format git repository. There should be a robust system for version selection and ensuring compatibilty with existing mods/version. Changing existing mods/versions to add a proposed mod should instead be submitted as a PR containg all version changes as well as the new mod.

There should also be a functionality to trigger a git action on a merge to publish the current modpack in a prism launcher importable zip file, and a server verison to run on a linux server. This should be handled as a release.

**In scope:**
- Discord slash commands for proposing, listing, removing, and rebuilding
- Deterministic Modrinth version resolution against a pinned MC version + loader
- Hard dependency / incompatibility detection via Modrinth's declared dependency graph
- Soft-conflict advisory via Anthropic API (Claude Haiku)
- Persistent packwiz repo with git history, committed by the bot
- server and client `.zip` export on demand
- Submitting PR to handle messy merges

**Out of scope:**
- Web UI
- CurseForge support (Modrinth only)
- Public hosting / Modrinth project distribution
- Automatic mod updates (handled separately via `packwiz update`)
- Multi-pack support (single pack per bot instance)

## 2. Architecture Overview

```
Discord ──slash commands──> Bot (long-running Python process)
                              │
                              ├──> Modrinth API (resolve versions, deps)
                              ├──> Anthropic API (soft-conflict check)
                              ├──> SQLite (proposal state)
                              └──> Local git repo (packwiz files)
                                     │
                                     └──push──> remote (GitHub / Gitea / etc.)
```

Single process, single host, outbound-only network from the bot's perspective (Discord uses a persistent websocket). No inbound ports required.

## 3. Tech Stack

- **Language:** Python 3.12
- **Discord:** `discord.py` ≥ 2.4 (slash commands + reaction events)
- **HTTP:** `httpx` (async, used throughout)
- **TOML write:** `tomli_w`; **TOML read:** stdlib `tomllib`
- **DB:** `sqlite3` (stdlib)
- **LLM:** `anthropic` Python SDK
- **External binary:** `packwiz` (Go binary on PATH) — used for `refresh` and `export`
- **Git:** invoked via `subprocess` (no GitPython dependency)

## 4. Configuration

All config via environment variables loaded at startup. Fail fast if any required var is missing.

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | yes | Bot token |
| `DISCORD_GUILD_ID` | yes | Server ID for command sync |
| `DISCORD_PROPOSAL_CHANNEL_ID` | yes | Channel where `/propose` is allowed |
| `PACK_DIR` | yes | Absolute path to packwiz repo on disk |
| `ANTHROPIC_API_KEY` | yes | Read by SDK from env |
| `MODRINTH_USER_AGENT` | yes | Format: `username/projectname/version (contact)` |
| `DB_PATH` | no | Default: `${PACK_DIR}/../bot.db` |
| `GIT_REMOTE` | no | Default: `origin` |
| `GIT_BRANCH` | no | Default: `dev` |
| `BOT_GIT_NAME` | no | Default: `discmod-bot` |
| `BOT_GIT_EMAIL` | no | Default: `discmod@localhost` |
| `MIN_APPROVALS` | no | Default: `1` (distinct ✅ reactions from non-proposers) |
| `BLOCK_ON_HARD_CONFLICTS` | no | Default: `false` |
| `PR_ON_HARD_CONFLICTS` | no | Default: `true`
| `LLM_MODEL` | no | Default: `claude-haiku-4-5-20251001` |

## 5. Repository Layout

The bot's own source code, kept separate from the packwiz repo:

```
discmod/
├── pyproject.toml
├── README.md
├── .env.example
├── src/
│   └── discmod/
│       ├── __init__.py
│       ├── main.py             # entrypoint, bot setup, slash commands
│       ├── config.py           # env loading, validation
│       ├── db.py               # sqlite schema + helpers
│       ├── modrinth.py         # API client, version resolution
│       ├── packwiz.py          # read/write .pw.toml, run packwiz CLI
│       ├── git_ops.py          # commit, push, log queries
│       ├── llm.py              # soft-conflict check
│       ├── conflicts.py        # hard-conflict detection from deps
│       └── commands/
│           ├── propose.py
│           ├── pack.py         # /pack status, list, remove, rebuild, export
│           └── reactions.py    # on_raw_reaction_add handler
└── tests/
    ├── test_modrinth.py
    ├── test_packwiz.py
    ├── test_conflicts.py
    └── fixtures/               # cached Modrinth JSON responses
```

The packwiz repo (separate, at `$PACK_DIR`) is created out-of-band with `packwiz init` and looks like:

```
modpack/
├── pack.toml
├── index.toml
└── mods/
    ├── sodium.pw.toml
    ├── lithium.pw.toml
    └── ...
```

## 6. Data Model

### 6.1 SQLite schema

```sql
CREATE TABLE IF NOT EXISTS proposals (
    message_id     INTEGER PRIMARY KEY,    -- Discord message ID of the proposal embed
    channel_id     INTEGER NOT NULL,
    thread_id      INTEGER,                -- Discord thread ID if one was created
    mod_url        TEXT NOT NULL,
    slug           TEXT NOT NULL,
    project_id     TEXT NOT NULL,          -- Modrinth project ID (stable across slug changes)
    proposer_id    INTEGER NOT NULL,
    proposer_name  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
        -- pending | merging | merged | rejected | failed | expired
    resolved_version TEXT,                  -- semantic version string after merge
    ai_summary     TEXT,
    error          TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS approvals (
    message_id     INTEGER NOT NULL,
    user_id        INTEGER NOT NULL,
    user_name      TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_id, user_id),
    FOREIGN KEY (message_id) REFERENCES proposals(message_id)
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_slug ON proposals(slug);
```

### 6.2 In-memory representations

```python
@dataclass(frozen=True)
class PackConfig:
    mc_version: str          # e.g. "1.21.1"
    loader: str              # "neoforge" | "fabric" | "forge" | "quilt"
    loader_version: str | None

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
    dependencies: list["DependencyRef"]
    client_side: str         # "required" | "optional" | "unsupported"
    server_side: str

@dataclass(frozen=True)
class DependencyRef:
    project_id: str | None
    version_id: str | None
    dependency_type: str     # "required" | "optional" | "incompatible" | "embedded"

@dataclass(frozen=True)
class PackMod:
    slug: str
    title: str
    description: str         # short Modrinth description, truncated to 300 chars
    project_id: str
    version_id: str
    version_number: str

@dataclass
class ConflictReport:
    hard: list[str]          # human-readable strings; empty if none
    soft: list["SoftConflict"]
    ai_summary: str

@dataclass
class SoftConflict:
    with_slug: str
    severity: str            # "low" | "medium" | "high"
    reason: str
```

## 7. Modrinth Integration

### 7.1 Client requirements

- Base URL: `https://api.modrinth.com/v2`
- Mandatory `User-Agent` header from config — Modrinth blocks generic UA strings
- Rate limit: 300 req/min per IP; client must respect `X-Ratelimit-Remaining` and back off when it drops below 10
- All requests async via `httpx.AsyncClient`, single shared instance
- 30s request timeout, 3 retries with exponential backoff on 5xx and 429

### 7.2 Functions

```python
async def fetch_project(slug_or_id: str) -> dict
    # GET /project/{id|slug}
    # Returns full project JSON. Slug is normalized from URL or raw input.

async def fetch_versions(
    slug_or_id: str,
    mc_version: str,
    loader: str,
) -> list[dict]
    # GET /project/{id|slug}/version?game_versions=["1.21.1"]&loaders=["neoforge"]
    # Note: query params must be JSON-array-encoded strings, not comma lists.
    # Returns versions newest-first.

async def resolve_version(
    slug: str,
    pack: PackConfig,
) -> ResolvedVersion
    # Selection rule:
    #   1. Filter to versions matching pack.mc_version AND pack.loader
    #   2. Prefer version_type == "release"; fall back to "beta", then "alpha"
    #   3. Within the chosen tier, take the newest (first in list)
    # Raise NoCompatibleVersion if list is empty after step 1.

async def fetch_project_by_id_batch(project_ids: list[str]) -> dict[str, dict]
    # GET /projects?ids=["...","..."] for dep-lookups.
    # Used to map dependency project_ids to slugs in one shot.
```

### 7.3 URL parsing

Accept any of:
- `https://modrinth.com/mod/sodium`
- `https://modrinth.com/mod/sodium/version/mc1.21-0.6.0`
- `sodium` (bare slug)
- `AANobbMI` (bare project ID — 8-char base62)

Normalize to slug-or-id, hand to API. Reject anything else with a user-facing error.

## 8. Conflict Detection

### 8.1 Hard conflicts (deterministic)

For a newly resolved version, walk `dependencies[]`:

```python
async def check_hard_conflicts(
    resolved: ResolvedVersion,
    current_pack: list[PackMod],
) -> list[str]:
    # Returns list of human-readable issue strings, e.g.:
    #   "declares incompatible with sodium (already in pack)"
    #   "requires fabric-api (not in pack)"
    #   "requires architectury (not in pack — would need to be added)"
```

Rules:
1. For each dep with `dependency_type == "incompatible"`: if its project_id matches any mod in the pack, emit a hard conflict.
2. For each dep with `dependency_type == "required"`: if its project_id is NOT in the pack, emit a "missing required dep" notice. This is informational by default; only blocks if `BLOCK_ON_HARD_CONFLICTS=true`.
3. Reverse check: for every existing pack mod, look at its stored `version_id`'s deps; if any declared `incompatible` against the new mod's `project_id`, emit a hard conflict.

Reverse check is required because dependency declarations aren't always symmetric.

### 8.2 Soft conflicts (LLM advisory)

Single call to Anthropic API, model from `LLM_MODEL`:

```python
async def soft_conflict_check(
    new_mod: PackMod,
    current_pack: list[PackMod],
) -> tuple[str, list[SoftConflict]]:
    # Returns (one_line_summary, conflicts)
```

System prompt:

> You analyze proposed additions to a Minecraft modpack for soft conflicts that a dependency graph won't catch: feature overlap (two shader pipelines, two inventory sorters), redundant systems, world-gen collisions, or performance concerns when combined. You output strictly valid JSON, no preamble, no markdown fences. Only flag genuine overlaps — two tech mods coexisting is fine.

User prompt template:

```
Current pack contents:
{for each mod: "- {slug}: {title} — {description}"}

Proposed addition:
- {slug}: {title}
- {description}

Output schema:
{
  "summary": "one-line description of what this mod does",
  "conflicts": [
    {"with": "slug", "severity": "low|medium|high", "reason": "..."}
  ]
}
```

Parsing:
- Strip ` ```json ` and ``` ``` ``` fences defensively even though instructed not to use them
- Validate against schema; on parse failure return `("", [])` and log
- Set `max_tokens` to 1024
- Never retry on parse failure — the LLM result is advisory, missing it is acceptable

Cost ceiling: this is called at most once per `/propose`, never on approval. With Haiku and ~30 mods in context, expect well under $0.001 per call.

## 9. Packwiz Integration

### 9.1 Reading the pack

`PACK_DIR/pack.toml` contains the pack config:

```toml
name = "our-modpack"
author = "us"
version = "0.1.0"
pack-format = "packwiz:1.1.0"

[index]
file = "index.toml"
hash-format = "sha256"
hash = "..."

[versions]
minecraft = "1.21.1"
neoforge = "21.1.95"
```

Parse this on startup and on every `/propose` to get `PackConfig`. The loader is whichever key under `[versions]` is not `minecraft`.

`PACK_DIR/mods/*.pw.toml` is the mod list. Each file:

```toml
name = "Sodium"
filename = "sodium-neoforge-0.6.0+mc1.21.jar"
side = "client"

[download]
url = "https://cdn.modrinth.com/data/AANobbMI/versions/.../sodium-neoforge-0.6.0+mc1.21.jar"
hash-format = "sha512"
hash = "abc123..."

[update]
[update.modrinth]
mod-id = "AANobbMI"
version = "xyz789"
```

Read every file in `mods/` to build `list[PackMod]`. Cache the `description` field by calling `fetch_project_by_id_batch` once with all project IDs at startup — store in a `pack_meta.json` alongside the repo (not in the repo, as it's derived). Refresh entries lazily as mods are added.

### 9.2 Writing a mod entry

```python
def write_mod_entry(
    slug: str,
    project: dict,
    resolved: ResolvedVersion,
    pack_dir: Path,
) -> Path
    # Writes pack_dir/mods/{slug}.pw.toml
    # Returns the path
```

Side field mapping:
- `client_side == "required"` and `server_side == "unsupported"` → `"client"`
- `client_side == "unsupported"` and `server_side == "required"` → `"server"`
- else → `"both"`

### 9.3 Running the packwiz CLI

After writing the entry, run:
```
packwiz refresh
```
in `$PACK_DIR`. This rehashes `index.toml`. Capture stdout/stderr; non-zero exit is a hard failure that aborts the merge and rolls back the file write.

For `.mrpack` export (called by `/pack export` or scheduled):
```
packwiz modrinth export
```
Produces `${pack_name}-${version}.mrpack` in `$PACK_DIR`. The command also accepts `-o /path/to/output.mrpack`.

## 10. Git Operations

All git operations via `subprocess.run(..., cwd=PACK_DIR, check=True)`. Set author identity via env or `-c user.name=... -c user.email=...` on each invocation rather than mutating global config.

```python
def commit_and_push(
    pack_dir: Path,
    message: str,
    body: str,
) -> str
    # git add -A
    # git -c user.name=... -c user.email=... commit -m message -m body
    # git push origin main
    # Returns commit SHA.
    # On push failure: leave commit in place, surface error. Do not auto-rollback —
    # the local repo is still consistent and the next push will catch up.
```

Commit message convention:
```
{verb} {slug} {version}

Proposed by {proposer_name} (discord id {proposer_id})
Approved by {approver_name} (discord id {approver_id})

Hard conflicts: none | <list>
Soft conflicts: none | <list>
```

`verb` ∈ {`Add`, `Remove`, `Update`}.

### 10.1 Authentication

Bot needs push rights. Recommended: SSH deploy key on the host, with the key path set via `GIT_SSH_COMMAND` env var if non-default. Alternative: HTTPS with a PAT stored via git credential helper. Spec assumes SSH; the bot does not manage credentials itself.

## 11. Discord Interface

### 11.1 Slash commands

| Command | Description |
|---|---|
| `/propose <modrinth_url>` | Propose a mod. Bot resolves version, runs conflict checks, posts an embed with reactions. |
| `/pack status` | Show pack name, MC version, loader, mod count, last commit SHA + author. |
| `/pack list [search]` | List all mods (paginated, 20 per page). Optional substring filter. |
| `/pack remove <slug>` | Initiate removal. Same approval flow as `/propose`. |
| `/pack rebuild` | Run `packwiz refresh` and commit if index changed. Admin-only. |
| `/pack export` | Run `packwiz modrinth export`, upload `.mrpack` as attachment (if under 25 MB) or post a path. |
| `/pack pending` | List proposals with `status='pending'`, with jump links. |

Restrict `/propose` to `$DISCORD_PROPOSAL_CHANNEL_ID`. Admin-only commands gated by a Discord role; role ID via optional `DISCORD_ADMIN_ROLE_ID` env var.

### 11.2 Proposal embed

```
┌─────────────────────────────────────────────┐
│ 🟢 Proposal: Sodium                          │  (color: green if no conflicts, amber if soft, red if hard)
│ Modrinth: https://modrinth.com/mod/sodium   │
│                                              │
│ Performance mod that rewrites Minecraft's   │  ← ai_summary
│ rendering engine for higher FPS.            │
│                                              │
│ Version: 0.6.0+mc1.21    Proposed by: @user │
│                                              │
│ ⛔ Hard conflicts                            │  (only shown if non-empty)
│ • declares incompatible with foo             │
│                                              │
│ ⚠️ Possible soft conflicts                   │  (only shown if non-empty)
│ • iris (medium): both manage shader pipeline│
│                                              │
│ React ✅ to approve, ❌ to reject            │
└─────────────────────────────────────────────┘
[bot reacts with ✅ and ❌]
```

If a Discord thread is created for the proposal, store its ID in `proposals.thread_id` and post merge/reject results there as well as in the main channel.

### 11.3 Reaction handling

`on_raw_reaction_add` event. Logic:

```
1. Ignore reactions from the bot itself.
2. Look up proposal by message_id. If not found, return.
3. If proposal.status != 'pending', return.
4. If emoji is ❌:
     a. If user is proposer OR has the admin role: mark rejected.
     b. Otherwise ignore.
5. If emoji is ✅:
     a. If user_id == proposer_id, remove the reaction and ignore.
     b. Insert into approvals (ignore on conflict).
     c. Count distinct approvers (excluding proposer). If < MIN_APPROVALS, return.
     d. Atomically transition status pending → merging:
          UPDATE proposals SET status='merging' WHERE message_id=? AND status='pending'
        If rowcount == 0, another handler already started; return.
     e. Run merge flow (section 12). On success, status='merged'. On failure,
        status='failed' and write error column.
     f. Post result in the channel (and thread if exists).
```

Step 5d is the concurrency guard — required even on single-process SQLite because reaction events can be processed concurrently within the asyncio loop.

### 11.4 Removal flow

Symmetric to add: `/pack remove sodium` creates a proposal with `mod_url='REMOVE:sodium'` and the merge step deletes `mods/sodium.pw.toml` instead of writing it. Same approval gating. AI is not consulted on removals.

## 12. Merge Flow (Add)

Pseudocode for the critical path. Must be idempotent on retry where possible.

```
async def execute_merge_add(proposal, approver):
    pack = read_pack_config(PACK_DIR)
    
    # Re-resolve at merge time, not propose time, so the version is fresh.
    project = await fetch_project(proposal.slug)
    resolved = await resolve_version(proposal.slug, pack)
    
    current = read_current_pack(PACK_DIR)
    
    # Re-check conflicts (pack may have changed since /propose).
    hard = await check_hard_conflicts(resolved, current)
    if hard and BLOCK_ON_HARD_CONFLICTS:
        raise MergeBlocked(f"hard conflicts: {hard}")
    
    # Write file.
    entry_path = write_mod_entry(proposal.slug, project, resolved, PACK_DIR)
    
    try:
        run_packwiz_refresh(PACK_DIR)
    except subprocess.CalledProcessError as e:
        entry_path.unlink(missing_ok=True)
        raise MergeFailed(f"packwiz refresh failed: {e.stderr}")
    
    try:
        sha = commit_and_push(
            PACK_DIR,
            f"Add {proposal.slug} {resolved.version_number}",
            build_commit_body(proposal, approver, hard, soft_from_db),
        )
    except subprocess.CalledProcessError as e:
        # File and refresh are already done; commit may or may not exist locally.
        # Re-running the merge would now find the mod present and bail with
        # "already in pack". Operator must intervene.
        raise MergePushFailed(str(e))
    
    update_proposal(
        proposal.message_id,
        status='merged',
        resolved_version=resolved.version_number,
        decided_at=now(),
    )
    return resolved, sha
```

### 12.1 Failure modes and recovery

| Failure | Visible to user | Recovery |
|---|---|---|
| Modrinth 404 on slug | "couldn't find mod" reply | None needed; proposal stays `pending` so it can be retried, or proposer cancels with ❌ |
| No compatible version | "no version for MC X / loader Y" | Same |
| `packwiz refresh` exit ≠ 0 | "packwiz refresh failed" with stderr | File write is rolled back; proposal returns to `pending` |
| Git commit fails | "commit failed" | proposal → `failed`; manual recovery required |
| Git push fails (after commit) | "pushed locally but remote failed" | proposal → `merged`, but operator must push manually or via `/pack rebuild` retry logic |
| LLM call fails | (silent) | Soft conflicts shown as empty; merge proceeds |
| Discord API failure mid-flow | log error | Merge state in DB is source of truth; on bot restart, reconcile (section 13) |

## 13. Startup & Reconciliation

On bot start:

1. Load and validate config.
2. Open DB; run schema migrations (just `CREATE TABLE IF NOT EXISTS` for v1).
3. Verify `$PACK_DIR` exists, is a git repo, has a `pack.toml`. Read pack config.
4. Verify `packwiz` binary is on PATH. Run `packwiz --version`.
5. Pull latest: `git pull --ff-only`. If non-fast-forward, log warning and continue (don't auto-merge).
6. Open Modrinth client; do a smoke request to `/` to verify connectivity.
7. Open Anthropic client; no startup request needed.
8. **Reconciliation:** for any proposals with `status='merging'`, mark `status='failed'` with error "bot restarted mid-merge — manual review required". This avoids double-merging if the bot crashed after committing but before updating status.
9. Sync slash commands to `$DISCORD_GUILD_ID`.
10. Begin event loop.

## 14. Error Handling & Logging

- Use `logging` stdlib with structured format: `%(asctime)s %(levelname)s %(name)s %(message)s`.
- Log to stdout; operator captures via systemd journal or equivalent.
- Every Discord-facing error has a user-visible message that is plain English (no stack traces in Discord).
- Every backend operation logs at INFO on entry and exit, WARNING on retried errors, ERROR on fatal.
- Modrinth and Anthropic responses are logged at DEBUG (full body) when `LOG_LEVEL=DEBUG`.

## 15. Testing

### 15.1 Unit tests (no network)

- `test_modrinth.py`: URL parsing, version selection rules, dep walking. Use cached JSON in `tests/fixtures/`.
- `test_packwiz.py`: TOML round-trip, side mapping, file naming.
- `test_conflicts.py`: hard conflict rules (forward + reverse), with hand-crafted dep graphs.

### 15.2 Integration tests (network, opt-in via `RUN_INTEGRATION=1`)

- Fetch a known-stable mod (Sodium) and verify resolution.
- Resolve against an MC version where no compatible release exists; assert `NoCompatibleVersion`.

### 15.3 Manual smoke test

A `scripts/smoke.py` that, given a slug and a pack config, runs the full resolve → write → refresh path in a tmpdir without Discord or git. Used to verify Modrinth API changes haven't broken anything.

## 16. Deployment

Target: a long-running process on a single Linux host (e.g., a jumphost on the user's home network).

Recommended: systemd service file:

```ini
[Unit]
Description=Collaborative Modpack Bot
After=network-online.target

[Service]
Type=simple
User=modpack
WorkingDirectory=/opt/discmod
EnvironmentFile=/etc/discmod/env
ExecStart=/opt/discmod/.venv/bin/python -m discmod.main
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

Filesystem layout on host:
```
/opt/discmod/               # bot source + venv
/srv/modpack/              # packwiz git repo (PACK_DIR)
/var/lib/discmod/           # bot.db
/etc/discmod/env            # env file, 0600, owned by modpack user
```

## 17. Build Order for Implementation

The recommended sequence for an LLM (or any developer) implementing this:

1. **`config.py`** — env loading, dataclass for full config, validation.
2. **`modrinth.py`** — async client, URL parsing, `fetch_project`, `fetch_versions`, `resolve_version`. Write tests against cached fixtures.
3. **`packwiz.py`** — `read_pack_config`, `read_current_pack`, `write_mod_entry`. Tests with a fixture pack dir.
4. **`conflicts.py`** — hard-conflict detection, both forward and reverse. Tests with synthetic dep graphs.
5. **`llm.py`** — soft-conflict check. Test the JSON parser with sample model outputs (including malformed ones).
6. **`git_ops.py`** — commit/push wrappers, log queries.
7. **`db.py`** — schema, helpers for proposal/approval CRUD.
8. **`commands/propose.py`** — slash command + embed assembly. Stub the merge for now.
9. **`commands/reactions.py`** — reaction handler with the atomic state transition.
10. **Merge flow** — wire steps 2–7 together into `execute_merge_add`.
11. **`commands/pack.py`** — status/list/remove/rebuild/export.
12. **Startup reconciliation** — finalize `main.py`.
13. **systemd unit + deployment docs.**

Each step should be independently testable. Don't proceed to step N+1 until step N has tests passing.

## 18. Acceptance Criteria

The system is considered done when:

- A user can run `/propose https://modrinth.com/mod/sodium` and see a proposal embed within 5 seconds.
- A second user reacting ✅ causes a commit to appear in the packwiz repo within 10 seconds.
- The proposer reacting ✅ is silently rejected.
- A proposal for a mod with no compatible version returns a clear error and no DB row in `merged` state.
- Restarting the bot mid-merge does not produce duplicate commits.
- `/pack export` produces a valid `.mrpack` that Prism Launcher / Modrinth App can install.
- Running with `BLOCK_ON_HARD_CONFLICTS=true`, a proposal that declares incompatibility with an existing pack mod is blocked at merge time even if it was approved.
- All unit tests pass; smoke script succeeds against live Modrinth.

## 19. Open Questions for the Operator

These should be decided before implementation, but are not technical blockers:

- `MIN_APPROVALS`: 1 or higher?
- Should `/pack remove` require approval, or be admin-only?
- Should the bot auto-`git pull` on a schedule to pick up out-of-band edits, or only on demand?
- Retention: should rejected/failed proposals be pruned from the DB after N days?
- Should the bot DM the proposer on merge/reject/fail, or only post in-channel?

## 20. Out-of-Band Operator Tasks

Things the bot does not do and that the operator must handle:

- `packwiz init` to create the initial repo
- `git remote add origin ...` and initial push
- Setting up SSH deploy key on the host
- Creating the Discord application, inviting the bot to the server with `applications.commands` + `bot` scopes and permissions: Send Messages, Embed Links, Add Reactions, Read Message History, Create Public Threads, Manage Messages (for cleanup)
- Periodic `packwiz update --all` to refresh pinned versions (this is intentionally separate from the proposal workflow)

---

## Appendix A — Reference: Modrinth API endpoints used

| Method | Path | Purpose |
|---|---|---|
| GET | `/v2/project/{id\|slug}` | Project metadata |
| GET | `/v2/project/{id\|slug}/version?game_versions=[...]&loaders=[...]` | Compatible versions |
| GET | `/v2/projects?ids=[...]` | Batch project lookup for dep resolution |

Base: `https://api.modrinth.com` — rate limit 300 req/min per IP, must send `User-Agent`.

## Appendix B — Reference: packwiz mod file schema

```toml
name = "<display name>"
filename = "<jar filename>"
side = "client" | "server" | "both"

[download]
url = "<direct CDN URL>"
hash-format = "sha512"
hash = "<hex>"

[update]
[update.modrinth]
mod-id = "<8-char project ID>"
version = "<8-char version ID>"
```

## Appendix C — Reference: Modrinth version dependency object

```json
{
  "version_id": null,
  "project_id": "P7dR8mSH",
  "file_name": null,
  "dependency_type": "required"
}
```

`dependency_type` ∈ `"required"` | `"optional"` | `"incompatible"` | `"embedded"`.
Either `version_id` or `project_id` may be null; in practice `project_id` is almost always populated for `required` and `incompatible`.
