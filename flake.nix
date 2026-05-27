{
  description = "discmod — Discord bot for collaborative Minecraft modpack curation";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forEachSystem = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forEachSystem (pkgs:
        let
          python = pkgs.python3;
          discmod = python.pkgs.buildPythonApplication {
            pname = "discmod";
            version = "0.1.0";
            pyproject = true;

            src = self;

            build-system = [ python.pkgs.setuptools ];

            dependencies = with python.pkgs; [
              discordpy
              httpx
              tomli-w
              anthropic
            ];

            # The test suite requires Discord + Modrinth fixtures; skip during
            # the Nix build and run separately with `nix develop`.
            doCheck = false;

            meta = {
              description = "Discord bot for collaborative Minecraft modpack curation via Modrinth";
              homepage = "https://github.com/guno327/discmod";
              license = nixpkgs.lib.licenses.mit;
              mainProgram = "discmod";
            };
          };
        in
        {
          default = discmod;
          inherit discmod;
        });

      # Expose as an overlay so downstream configs can reference pkgs.discmod.
      overlays.default = final: prev: {
        discmod = self.packages.${final.system}.default;
      };

      # The NixOS service module.  Import in your configuration like:
      #
      #   inputs.discmod.nixosModules.default
      #
      nixosModules.default = import ./nix/module.nix self;
    };
}
