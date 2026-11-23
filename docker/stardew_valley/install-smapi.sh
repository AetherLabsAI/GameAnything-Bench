#!/usr/bin/env bash
# install-smapi.sh GAME_DIR  -- SMAPI 4.5.2 into a Stardew Valley 1.6.15 Linux folder (what gab's stardew_valley adapter expects in
# $GAB_STARDEW_GAME_DIR). Source: the official GitHub release asset (Pathoschild/SMAPI 4.5.2, LGPL-3.0), sha256 pinned; the
# installer runs non-interactively (--install --no-prompt --game-path). It adds StardewModdingAPI*, smapi-internal/,
# Mods/{ConsoleCommands,SaveBackup}, steam_appid.txt and swaps the launcher (StardewValley -> StardewValley-original); the game
# itself is not changed. Checks StardewModdingAPI.dll against the adapter's pin afterwards. Needs no account and no network
# besides github.com. Re-running is harmless (the installer updates in place).
set -euo pipefail
G=${1:?usage: install-smapi.sh GAME_DIR}
VER=4.5.2
URL=https://github.com/Pathoschild/SMAPI/releases/download/$VER/SMAPI-$VER-installer.zip
ZIP_SHA=dd01ddca7b566bfe0d3b3d2d03833496abc56c53da976241f2ab443f5484acc4     # GitHub's asset digest
DLL_SHA=d0d40338afce227c7c69af555d7abd4a7077a4c9cf5fe2c2a94ae685cc79d88a     # = SMAPI_SHA in gab/game/stardew_valley/adapter.py
[ -f "$G/Stardew Valley.dll" ] || { echo "install-smapi: no 'Stardew Valley.dll' in $G" >&2; exit 3; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
curl -fsSL --retry 3 --max-time 600 -o "$T/smapi.zip" "$URL"
echo "$ZIP_SHA  $T/smapi.zip" | sha256sum -c --quiet - || { echo "install-smapi: SMAPI installer sha256 mismatch" >&2; exit 4; }
mkdir "$T/x" && python3 -I -m zipfile -e "$T/smapi.zip" "$T/x"
I="$T/x/SMAPI $VER installer/internal/linux"
chmod +x "$I/SMAPI.Installer"
"$I/SMAPI.Installer" --install --no-prompt --game-path "$G" </dev/null
got=$(sha256sum "$G/StardewModdingAPI.dll" | cut -d' ' -f1)
[ "$got" = "$DLL_SHA" ] || { echo "install-smapi: StardewModdingAPI.dll is $got, not SMAPI $VER" >&2; exit 5; }
for m in ConsoleCommands SaveBackup; do [ -d "$G/Mods/$m" ] || { echo "install-smapi: Mods/$m missing" >&2; exit 5; }; done
echo "install-smapi: SMAPI $VER installed in $G (StardewModdingAPI.dll sha256 ok)"
