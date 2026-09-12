{
  description = "Hydra Nexus - WeeChat Matrix Client (Python)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, utils }:
    utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        apps.default = {
          type = "app";
          program = "${pkgs.writeShellScriptBin "weematrix" ''
            exec ${pkgs.weechat}/bin/weechat -p ${pkgs.weechatScripts.weechat-matrix}
          ''}/bin/weematrix";
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [ 
            pkgs.weechat 
            pkgs.weechatScripts.weechat-matrix 
          ];
        };
      }
    );
}
