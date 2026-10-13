{
  description = "dishcord development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
        };
        pythonEnv = pkgs.python3.withPackages (pythonPackages: [
          pythonPackages.pytest
          pythonPackages.pytest-asyncio
          pythonPackages.black
          pythonPackages.ruff
          pythonPackages.discordpy
        ]);
      in {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            pythonEnv
          ];

          PYTHONPATH = "${./src}";
        };
      }
    );
}
