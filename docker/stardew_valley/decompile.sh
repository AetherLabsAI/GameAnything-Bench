#!/usr/bin/env bash
# decompile.sh [GAME_DIR [OUT_DIR]]  -- optional: the decompiled game sources the Stardew tasks offer agents (`game info` ->
# decompiled_sources = /lab/sd-sources in the sandbox, as the adapter expects). Decompiles Stardew Valley.dll and
# StardewValley.GameData.dll of YOUR game folder with ILSpy's command line (ilspycmd, MIT, pinned version from nuget.org) into
# OUT_DIR (default <game dir>/../sd-sources, mode 700 parent). The docker backend mounts it read-only at /lab/sd-sources when it
# exists (config stardew_sources_dir). Derived from the commercial game: stays on this machine, never in an image or the repo.
# Runs in the stardew_valley image (it has the .NET 8 SDK) with network for the one tool download; no account needed.
#   IMAGE=<ref> (default ghcr.io/aetherlabsai/gameanything-bench-stardew_valley:local, built by docker/build.sh stardew_valley)
set -euo pipefail
G=${1:-${GAB_STARDEW_GAME_DIR:-${GAB_COMMERCIAL_DIR:-$HOME/.gab/commercial}/stardew_valley/game}}
G=$(cd "$G" && pwd); O=${2:-$(dirname "$G")/sd-sources}
IMAGE=${IMAGE:-ghcr.io/aetherlabsai/gameanything-bench-stardew_valley:local}
ILSPY=9.1.0.7988
[ -f "$G/Stardew Valley.dll" ] || { echo "decompile: no 'Stardew Valley.dll' in $G" >&2; exit 3; }
mkdir -p "$O"; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; chmod 777 "$T"
# as the image's player (dotnet's first run fails for a uid without a passwd entry); the output is copied out as you
docker run --rm -e HOME=/tmp/h -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 \
  -v "$G:/lab/game:ro" -v "$T:/out" "$IMAGE" bash -c "set -e; trap 'chmod -R a+rwX /out/* 2>/dev/null || true' EXIT; mkdir -p /tmp/h
  dotnet tool install --tool-path /tmp/ilspy ilspycmd --version $ILSPY >/dev/null
  for d in 'Stardew Valley' StardewValley.GameData; do
    [ -f \"/lab/game/\$d.dll\" ] || continue
    /tmp/ilspy/ilspycmd --disable-updatecheck -p -o \"/out/\$d\" -r /lab/game -r /lab/game/smapi-internal \"/lab/game/\$d.dll\" > /tmp/ilspy.log 2>&1 || { tail -20 /tmp/ilspy.log; exit 1; }
    echo \"decompiled \$d: \$(find \"/out/\$d\" -name '*.cs' | wc -l) .cs files\"
  done
  echo \"ILSpy (ilspycmd $ILSPY) of the user's Stardew Valley build; for reading only, not redistributable\" > /out/SOURCE.txt"
rm -rf "$O.tmp" && cp -r "$T" "$O.tmp" && rm -rf "$O" && mv "$O.tmp" "$O" && chmod -R go+rX,go-w "$O"
echo "decompile: sources in $O ($(du -sh "$O" | cut -f1)); gab mounts them at /lab/sd-sources"
