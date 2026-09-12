{
  description = "Hydra Nexus - Local Matrix Homeserver (Conduit)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, utils }:
    utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = [ pkgs.matrix-conduit pkgs.matterbridge ];
        };

        apps.conduit = {
          type = "app";
          program = "${pkgs.writeShellScriptBin "run-conduit" ''
            export CONDUIT_CONFIG=${./conduit.toml}
            exec ${pkgs.matrix-conduit}/bin/conduit
          ''}/bin/run-conduit";
        };

        apps.bridge = {
          type = "app";
          program = "${pkgs.writeShellScriptBin "run-bridge" ''
            exec ${pkgs.matterbridge}/bin/matterbridge -conf ${./matterbridge.toml}
          ''}/bin/run-bridge";
        };

        apps.default = self.apps.${system}.conduit;
      }
    );
}
