# dicmod

A Discord bot for collaboratively curating a Minecraft modpack via Modrinth. Mods are proposed with a slash command, approved by reaction, then automatically resolved and committed to a [packwiz](https://packwiz.infra.link/) git repository.

## Features

- `/propose <url>` — propose a mod by Modrinth URL or slug
- Reaction-based approval flow (`✅` / `❌`)
- Deterministic version resolution against a pinned MC version + loader
- Hard dependency / incompatibility detection via Modrinth's dependency graph
- Soft-conflict advisory via Claude (Haiku) — flags feature overlaps, redundant systems, etc.
- Full git history of pack changes, committed by the bot
- `/pack export` — exports a `.mrpack` importable by Prism Launcher / Modrinth App
- Removal proposals via `/pack remove <slug>`

---

## Prerequisites

- Python 3.12+
- [`packwiz`](https://packwiz.infra.link/) binary on `PATH`
- A Discord application with a bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- An Anthropic API key (for soft-conflict checks)
- A git repository initialised with `packwiz init` (the pack repo)

### Discord bot permissions

When inviting the bot, grant the following:
- Scopes: `bot`, `applications.commands`
- Permissions: Send Messages, Embed Links, Add Reactions, Read Message History, Create Public Threads, Manage Messages

---

## Installation

```bash
git clone <repo-url> /opt/dicmod
cd /opt/dicmod
python3 -m venv .venv
.venv/bin/pip install -e .
```

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to get started:

```bash
cp .env.example /etc/dicmod/env
chmod 600 /etc/dicmod/env
```

### Required

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from the Discord Developer Portal |
| `DISCORD_GUILD_ID` | ID of the Discord server to register commands on |
| `DISCORD_PROPOSAL_CHANNEL_ID` | Channel where `/propose` is permitted |
| `PACK_DIR` | Absolute path to the packwiz git repository on disk |
| `ANTHROPIC_API_KEY` | API key for soft-conflict checks via Claude |
| `MODRINTH_USER_AGENT` | Identifies your bot to Modrinth — format: `username/project/version (contact)` |

### Optional

| Variable | Default | Description |
|---|---|---|
| `DB_PATH` | `${PACK_DIR}/../bot.db` | Path to the SQLite database |
| `GIT_REMOTE` | `origin` | Git remote to push commits to |
| `GIT_BRANCH` | `dev` | Branch to commit and push to |
| `BOT_GIT_NAME` | `dicmod-bot` | Author name used in pack commits |
| `BOT_GIT_EMAIL` | `dicmod@localhost` | Author email used in pack commits |
| `MIN_APPROVALS` | `1` | Number of distinct non-proposer `✅` reactions required to merge |
| `BLOCK_ON_HARD_CONFLICTS` | `false` | If `true`, merges are blocked when hard conflicts are detected |
| `PR_ON_HARD_CONFLICTS` | `true` | If `true`, hard conflicts trigger a PR instead of a direct merge |
| `LLM_MODEL` | `claude-haiku-4-5-20251001` | Anthropic model used for soft-conflict checks |
| `DISCORD_ADMIN_ROLE_ID` | _(none)_ | Role ID whose members can use admin-only commands. If unset, all users have admin access |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Pack repository setup

The bot requires a packwiz repository to already exist at `PACK_DIR`. If you don't have one:

```bash
mkdir -p /srv/modpack && cd /srv/modpack
packwiz init   # follow the prompts for MC version, loader, etc.
git init
git remote add origin <your-remote-url>
git add -A && git commit -m "Init pack"
git push -u origin dev
```

The bot needs push access. The recommended approach is an SSH deploy key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/dicmod_deploy -N ""
# Add ~/.ssh/dicmod_deploy.pub as a deploy key on your remote
export GIT_SSH_COMMAND="ssh -i ~/.ssh/dicmod_deploy"
```

---

## Running

### Directly

```bash
cd /opt/dicmod
source /etc/dicmod/env   # or set env vars however you prefer
.venv/bin/python -m dicmod.main
```

### systemd (recommended)

A service file is provided at `deploy/dicmod.service`. Install it:

```bash
cp deploy/dicmod.service /etc/systemd/system/dicmod.service
systemctl daemon-reload
systemctl enable --now dicmod
journalctl -u dicmod -f   # follow logs
```

The service runs as the `modpack` user. Create it if needed:

```bash
useradd -r -s /usr/sbin/nologin modpack
chown -R modpack: /opt/dicmod /srv/modpack /var/lib/dicmod
```

---

## Commands

| Command | Description |
|---|---|
| `/propose <modrinth_url>` | Propose a mod by Modrinth URL or slug |
| `/pack status` | Show MC version, loader, mod count, and last commit |
| `/pack list [search]` | List mods in the pack, with optional substring filter |
| `/pack remove <slug>` | Propose removal of a mod (same approval flow as `/propose`) |
| `/pack pending` | List open proposals with jump links |
| `/pack export` | Export the pack as a `.mrpack` file |
| `/pack rebuild` | Re-run `packwiz refresh` and commit if the index changed *(admin)* |

---

## NixOS module

A NixOS module and Nix package are provided via the flake at `github:guno327/dicmod`.

### 1. Add the flake input and configure the service

```nix
# flake.nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    dicmod.url  = "github:guno327/dicmod";
  };

  outputs = { nixpkgs, dicmod, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        dicmod.nixosModules.default
        ./hardware-configuration.nix

        ({ pkgs, ... }: {
          services.dicmod = {
            enable = true;

            # Non-secret config set directly in Nix.
            modrinthUserAgent = "guno327/dicmod/0.1.0 (you@example.com)";
            extraPackages     = [ pkgs.packwiz ];

            # Discord IDs: not security-sensitive (any server member can see
            # them with Developer Mode on), but leave these null and supply
            # them via environmentFile if your config is in a public repo.
            discordGuildId           = "123456789012345678";
            discordProposalChannelId = "987654321098765432";

            # Path to a secrets file readable only by root / the service user.
            # Must define DISCORD_TOKEN and ANTHROPIC_API_KEY at minimum.
            environmentFile = "/etc/dicmod/secrets.env";
          };
        })
      ];
    };
  };
}
```

### 2. Create the secrets file

```bash
install -m 0400 /dev/null /etc/dicmod/secrets.env
# then populate it:
cat >> /etc/dicmod/secrets.env <<'EOF'
DISCORD_TOKEN=your-bot-token-here
ANTHROPIC_API_KEY=your-api-key-here
# optionally, if you left the IDs out of the Nix config:
# DISCORD_GUILD_ID=123456789012345678
# DISCORD_PROPOSAL_CHANNEL_ID=987654321098765432
EOF
```

### 3. Set up the pack repository

The module creates `/var/lib/dicmod/modpack` but does not initialise it.
Run these once as the `dicmod` service user (or as root with `sudo -u dicmod`):

```bash
cd /var/lib/dicmod/modpack
packwiz init          # follow prompts for MC version + loader
git init
git remote add origin <your-remote-url>
git add -A && git commit -m "Init pack"
git push -u origin dev
```

For push access, create an SSH deploy key and point the service at it
by adding `GIT_SSH_COMMAND=ssh -i /var/lib/dicmod/.ssh/deploy_key` to
`environmentFile`, then install the public half as a deploy key on your remote.

### Module options reference

| Option | Type | Default | Notes |
|---|---|---|---|
| `enable` | bool | `false` | |
| `package` | package | flake default | Override to pin a version |
| `user` / `group` | string | `"dicmod"` | Service account created automatically |
| `stateDir` | string | `/var/lib/dicmod` | Root of all bot state |
| `packDir` | string | `stateDir/modpack` | Path to the packwiz git repo |
| `discordGuildId` | string\|null | `null` | Set here or in `environmentFile` |
| `discordProposalChannelId` | string\|null | `null` | Set here or in `environmentFile` |
| `discordAdminRoleId` | string\|null | `null` | |
| `modrinthUserAgent` | string | *(required)* | |
| `dbPath` | string\|null | `null` | Defaults to `packDir/../bot.db` |
| `gitRemote` | string | `"origin"` | |
| `gitBranch` | string | `"dev"` | |
| `botGitName` / `botGitEmail` | string | `"dicmod-bot"` / `"dicmod@localhost"` | Git author identity |
| `minApprovals` | int | `1` | |
| `blockOnHardConflicts` | bool | `false` | |
| `prOnHardConflicts` | bool | `true` | |
| `llmModel` | string | `claude-haiku-4-5-20251001` | |
| `logLevel` | enum | `"INFO"` | |
| `environmentFile` | path\|null | `null` | Systemd `EnvironmentFile=` for secrets |
| `extraPackages` | \[package\] | `[]` | Add `pkgs.packwiz` here |

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

For a full end-to-end smoke test against the live Modrinth API:

```bash
python scripts/smoke.py sodium 1.21.1 fabric
```
