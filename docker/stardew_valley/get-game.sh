#!/usr/bin/env bash
# get-game.sh  -- download YOUR copy of Stardew Valley (Steam app 413150, Linux depot 413153) with SteamCMD and install SMAPI 4.5.2
# into it, for gab's stardew_valley adapter on the docker host (the folder is mounted read-only into each trial container; nothing
# of the game goes into an image). Run it yourself, in your own terminal (tmux is fine): SteamCMD asks for the Steam password
# and the Steam Guard code itself (hidden input). Nothing is passed on a command line or written by this script.
#   docker/stardew_valley/get-game.sh                      default folders below
#   GAB_COMMERCIAL_DIR=/path docker/stardew_valley/get-game.sh
#   KEEP_STEAM_LOGIN=1 ...                                 keep SteamCMD's cached login (default: removed at the end)
# Folders: $GAB_COMMERCIAL_DIR/steamcmd (SteamCMD + its cache), $GAB_COMMERCIAL_DIR/stardew_valley/game (the game).
# Access: $GAB_COMMERCIAL_DIR and stardew_valley/ are mode 700 (no other host user reaches the game); inside game/ everything is
# world-readable, because the container's game user (uid 1000) reads it through the read-only bind mount, which skips the parents.
# Then: export GAB_STARDEW_GAME_DIR=$GAB_COMMERCIAL_DIR/stardew_valley/game   (or gab setup --set stardew_game_dir PATH)
# SteamCMD runs in a small container (docker/stardew_valley/steamcmd.Dockerfile: the host has no 32-bit libraries) as your uid.
set -euo pipefail
cd "$(dirname "$0")"
C=${GAB_COMMERCIAL_DIR:-$HOME/.gab/commercial}
SC=$C/steamcmd; G=$C/stardew_valley/game
APP=413150; DEPOT=413153
MANIFEST=6005910083361727734      # depot 413153 public manifest = build 16826371 = 1.6.15 (api.steamcmd.net, checked 2026-11-05)
GAME_SHA=f3e97f01d3fd2b1e6094fc8d2b59950aa6cb9d6cd1bf1b39d72d58edda8aad12     # Stardew Valley.dll 1.6.15 (adapter.py GAME_SHA)
IMG=gab-steamcmd:local
umask 077
mkdir -p "$SC" "$G"; chmod 700 "$C" "$SC" "$C/stardew_valley"
[ -t 0 ] || { echo "get-game: run this in an interactive terminal (SteamCMD asks for the password / Steam Guard code)" >&2; exit 2; }
docker image inspect $IMG >/dev/null 2>&1 || docker build -q -f steamcmd.Dockerfile -t $IMG .
[ -x "$SC/steamcmd.sh" ] || curl -fsSL --max-time 300 https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar -xz -C "$SC"
steamcmd() { docker run --rm -it --user "$(id -u):$(id -g)" -e HOME=/steam -v "$SC:/steam" -v "$G:/game" $IMG /steam/steamcmd.sh "$@"; }
dll_sha() { sha256sum "$G/Stardew Valley.dll" 2>/dev/null | cut -d' ' -f1; }

read -r -p "Steam user name (the account that owns Stardew Valley): " SU
[ -n "$SU" ] || exit 2
echo "== SteamCMD: log in as $SU (it asks for the password and the Steam Guard code), download app $APP for Linux into $G"
# +login with the user name only: SteamCMD prompts for the password itself (never on a command line)
steamcmd +@sSteamCmdForcePlatformType linux +force_install_dir /game +login "$SU" +app_update $APP validate +quit || true
if [ "$(dll_sha)" != "$GAME_SHA" ]; then
  echo "== Stardew Valley.dll is '$(dll_sha)', not 1.6.15: fetching the pinned 1.6.15 manifest (depot $DEPOT manifest $MANIFEST)"
  steamcmd +@sSteamCmdForcePlatformType linux +login "$SU" +download_depot $APP $DEPOT $MANIFEST +quit || true
  D="$SC/linux32/steamapps/content/app_$APP/depot_$DEPOT"
  [ -f "$D/Stardew Valley.dll" ] || D="$SC/steamapps/content/app_$APP/depot_$DEPOT"
  [ -f "$D/Stardew Valley.dll" ] || { echo "get-game: download_depot left no game in $D" >&2; exit 1; }
  find "$G" -mindepth 1 -delete; cp -a "$D/." "$G/"; rm -rf "$D"
fi
[ "$(dll_sha)" = "$GAME_SHA" ] || { echo "get-game: Stardew Valley.dll is '$(dll_sha)', expected 1.6.15 ($GAME_SHA)" >&2; exit 1; }
echo "== Stardew Valley 1.6.15 ok ($(du -sh "$G" | cut -f1)). Installing SMAPI 4.5.2"
bash ./install-smapi.sh "$G"
rm -rf "$G/steamapps"                              # SteamCMD's app manifest (not part of the game)
if [ "${KEEP_STEAM_LOGIN:-0}" != 1 ]; then         # SteamCMD caches a login token: removed (the next download asks again)
  rm -f "$SC/config/config.vdf" "$SC"/ssfn* "$SC/Steam/config/config.vdf" "$SC/.steam/config/config.vdf" 2>/dev/null || true
  echo "== SteamCMD's cached login removed (KEEP_STEAM_LOGIN=1 keeps it)"
fi
chmod -R go+rX,go-w "$G"
echo "== done. The game folder: $G"
echo "   export GAB_STARDEW_GAME_DIR=$G      # or: python3 -m gab setup --set stardew_game_dir $G"
