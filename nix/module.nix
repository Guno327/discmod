self:
{ config, lib, pkgs, ... }:

let
  inherit (lib)
    mkEnableOption mkOption mkIf
    types literalExpression optionalAttrs;
  cfg = config.services.discmod;
in
{
  options.services.discmod = {
    enable = mkEnableOption "discmod collaborative modpack bot";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.system}.default;
      defaultText = literalExpression "discmod.packages.\${system}.default";
      description = "The discmod package to use.";
    };

    user = mkOption {
      type = types.str;
      default = "discmod";
      description = "System user account that runs the bot.";
    };

    group = mkOption {
      type = types.str;
      default = "discmod";
      description = "System group for the bot user.";
    };

    stateDir = mkOption {
      type = types.str;
      default = "/var/lib/discmod";
      description = "Root state directory owned by the service user.";
    };

    # ---------- required settings ----------

    discordGuildId = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        Discord server (guild) ID.  Not a secret in the security sense
        (visible to any server member with Developer Mode on), but if your
        NixOS config is a public repository you may prefer to supply it via
        `environmentFile` as `DISCORD_GUILD_ID=...` and leave this null.
      '';
      example = "123456789012345678";
    };

    discordProposalChannelId = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        Channel ID in which `/propose` is accepted.  Same privacy
        considerations as `discordGuildId` — set here or via `environmentFile`.
      '';
      example = "987654321098765432";
    };

    packDir = mkOption {
      type = types.str;
      default = "/var/lib/discmod/modpack";
      description = ''
        Absolute path to the packwiz git repository on disk.
        The directory must be initialised out-of-band with `packwiz init`
        and have a git remote configured for push access.
      '';
    };

    modrinthUserAgent = mkOption {
      type = types.str;
      description = "User-Agent string sent to the Modrinth API.";
      example = "myuser/my-modpack/0.1.0 (contact@example.com)";
    };

    # ---------- optional settings ----------

    dbPath = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        Path to the SQLite database file.
        Defaults to ''${packDir}/../bot.db'' when null.
      '';
      example = "/var/lib/discmod/bot.db";
    };

    discordAdminRoleId = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "Discord role ID whose members can run admin-only commands.";
    };

    gitRemote = mkOption {
      type = types.str;
      default = "origin";
      description = "Git remote name used for push.";
    };

    gitBranch = mkOption {
      type = types.str;
      default = "dev";
      description = "Git branch the bot commits to.";
    };

    gitMainBranch = mkOption {
      type = types.str;
      default = "main";
      description = "Branch that `discmod-release` merges into and tags.";
    };

    botGitName = mkOption {
      type = types.str;
      default = "discmod-bot";
      description = "Git author name used in bot commits.";
    };

    botGitEmail = mkOption {
      type = types.str;
      default = "discmod@localhost";
      description = "Git author email used in bot commits.";
    };

    minApprovals = mkOption {
      type = types.ints.unsigned;
      default = 1;
      description = "Number of distinct ✅ reactions (from non-proposers) required to merge. Set to 0 to auto-merge on propose.";
    };

    blockOnHardConflicts = mkOption {
      type = types.bool;
      default = false;
      description = "Abort the merge when hard (incompatibility) conflicts are detected.";
    };

    prOnHardConflicts = mkOption {
      type = types.bool;
      default = true;
      description = "Open a PR instead of a direct commit when hard conflicts are detected.";
    };

    llmModel = mkOption {
      type = types.str;
      default = "claude-haiku-4-5-20251001";
      description = "Anthropic model used for soft-conflict advisory.";
    };

    logLevel = mkOption {
      type = types.enum [ "DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL" ];
      default = "INFO";
      description = "Log verbosity level.";
    };

    # ---------- secrets ----------

    environmentFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = ''
        Path to a file containing secret environment variables loaded by
        systemd at service start.  The file must define at minimum:

          DISCORD_TOKEN=<bot-token>
          ANTHROPIC_API_KEY=<api-key>

        Recommended permissions: mode 0400, owned by root or the service
        user.  The path is passed directly to systemd EnvironmentFile=.
      '';
      example = "/etc/discmod/secrets.env";
    };

    # ---------- release CLI ----------

    enableReleaseCli = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Add `discmod-release` (and `discmod`) to `environment.systemPackages`
        so the release tool is available on the system PATH for any user.
        Disable if you prefer to manage the package yourself.
      '';
    };

    # ---------- extra runtime packages ----------

    extraPackages = mkOption {
      type = types.listOf types.package;
      default = [ ];
      description = ''
        Additional packages placed on PATH for the service.
        `packwiz` must be present at runtime — add it here if it is not
        already on the system PATH.  Example:

          extraPackages = [ pkgs.packwiz ];
      '';
      example = literalExpression "[ pkgs.packwiz ]";
    };
  };

  config = mkIf cfg.enable {
    environment.systemPackages = lib.mkIf cfg.enableReleaseCli [ cfg.package ];

    users.users.${cfg.user} = {
      isSystemUser = true;
      group = cfg.group;
      home = cfg.stateDir;
      createHome = false;
      description = "discmod bot service account";
    };

    users.groups.${cfg.group} = { };

    systemd.tmpfiles.rules = [
      "d ${cfg.stateDir}  0750 ${cfg.user} ${cfg.group} - -"
      "d ${cfg.packDir}   0750 ${cfg.user} ${cfg.group} - -"
    ];

    systemd.services.discmod = {
      description = "discmod collaborative modpack bot";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];

      # git and openssh are needed for commit/push; packwiz must come from
      # extraPackages because it is not a default system dependency.
      path = [ pkgs.git pkgs.openssh ] ++ cfg.extraPackages;

      environment = {
        PACK_DIR = cfg.packDir;
        MODRINTH_USER_AGENT = cfg.modrinthUserAgent;
        GIT_REMOTE = cfg.gitRemote;
        GIT_BRANCH = cfg.gitBranch;
        GIT_MAIN_BRANCH = cfg.gitMainBranch;
        BOT_GIT_NAME = cfg.botGitName;
        BOT_GIT_EMAIL = cfg.botGitEmail;
        MIN_APPROVALS = toString cfg.minApprovals;
        BLOCK_ON_HARD_CONFLICTS = if cfg.blockOnHardConflicts then "true" else "false";
        PR_ON_HARD_CONFLICTS = if cfg.prOnHardConflicts then "true" else "false";
        LLM_MODEL = cfg.llmModel;
        LOG_LEVEL = cfg.logLevel;
      }
      // optionalAttrs (cfg.discordGuildId != null) {
        DISCORD_GUILD_ID = cfg.discordGuildId;
      }
      // optionalAttrs (cfg.discordProposalChannelId != null) {
        DISCORD_PROPOSAL_CHANNEL_ID = cfg.discordProposalChannelId;
      }
      // optionalAttrs (cfg.dbPath != null) { DB_PATH = cfg.dbPath; }
      // optionalAttrs (cfg.discordAdminRoleId != null) {
        DISCORD_ADMIN_ROLE_ID = cfg.discordAdminRoleId;
      };

      serviceConfig = {
        Type = "simple";
        User = cfg.user;
        Group = cfg.group;
        ExecStart = "${cfg.package}/bin/discmod";
        Restart = "on-failure";
        RestartSec = "10s";

        # Hardening
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        # The bot writes to packDir and stateDir; allow both explicitly.
        ReadWritePaths = lib.unique [ cfg.stateDir cfg.packDir ];
        # Prevent the process from acquiring new capabilities.
        CapabilityBoundingSet = "";
        LockPersonality = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        SystemCallFilter = "@system-service";
        SystemCallErrorNumber = "EPERM";
      }
      // optionalAttrs (cfg.environmentFile != null) {
        EnvironmentFile = cfg.environmentFile;
      };
    };
  };
}
