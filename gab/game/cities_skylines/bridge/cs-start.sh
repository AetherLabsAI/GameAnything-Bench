#!/usr/bin/env bash
# gab copy of gamebench hosting/container/cities/start-game.sh + TMPDIR passed to the game (the settings-save fix).
# Start Cities: Skylines 1 (Linux native, Unity Mono) directly, skipping the Paradox launcher ("dowser"); the Steam client
# in this container is logged in, so SteamAPI_Init succeeds normally (no DRM bypass). Mods: the game itself loads
# ~/.local/share/Colossal Order/Cities_Skylines/Addons/Mods/<Mod>/ (compiling Source/*.cs if there is no DLL).
# GPU: with VGL=1 (+ --gpus) re-executes itself under VirtualGL's EGL back end, like Valheim. Extra args: $CS_ARGS.
# The game runs inside Steam's scout runtime (ubuntu12_32/steam-runtime/run.sh), as Steam itself would start it: outside
# it, the 2015-era libsteam_api crashed in steamclient.so ("free(): invalid pointer") on some starts. A start that dies
# within CS_RETRY_WINDOW seconds is retried (CS_RETRIES times). The game window is raised above Steam's own window.
cd "${GAME_DIR:?}" || exit 1
if [ "${VGL:-0}" = 1 ] && [ -z "${CS_IN_VGL:-}" ] && command -v vglrun >/dev/null; then
  export CS_IN_VGL=1
  exec vglrun -d egl "$0" "$@"
fi
export SteamAppId=255710 SteamGameId=255710
# steamclient.so writes a minidump on every internal assert and tries to upload it via dlopen("libcurl.so"); that name
# does not exist here (only libcurl.so.4), and the failed dlopen path corrupted the heap (free(): invalid pointer /
# malloc() aborts inside ld.so during PlatformService.Initialize). Give it the library.
mkdir -p /tmp/cslib && ln -sf /usr/lib/x86_64-linux-gnu/libcurl.so.4 /tmp/cslib/libcurl.so
export LD_LIBRARY_PATH=/tmp/cslib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
RT=$HOME/.steam/debian-installation/ubuntu12_32/steam-runtime/run.sh
[ -x "$RT" ] && [ "${CS_RUNTIME:-0}" = 1 ] || RT=      # off by default (no help against the crashes below)
( for _ in $(seq 1 120); do                        # raise the game window once it exists (Steam's window may cover it)
    w=$(xdotool search --name "^Cities: Skylines$" 2>/dev/null | head -1)
    [ -n "$w" ] && { xdotool windowmove "$w" 0 0 windowraise "$w" windowactivate "$w" 2>/dev/null; sleep 5; xdotool windowraise "$w" 2>/dev/null; break; }
    sleep 2; done ) &
for try in $(seq 1 "${CS_RETRIES:-3}"); do
  t0=$(date +%s)
  # shellcheck disable=SC2086
  # A minimal environment: steamclient.so copies the process environment into a fixed buffer ("Couldn't fit all the
  # environmental variables in allotted space") and then corrupts the heap (malloc()/free() aborts, broken IPC pipe).
  E=(HOME="$HOME" USER="${USER:-player}" PATH=/usr/local/bin:/usr/bin:/bin DISPLAY="$DISPLAY" LANG=C.UTF-8 SteamAppId=255710 SteamGameId=255710)
  for v in CS_INSTANCE LD_PRELOAD LD_LIBRARY_PATH XDG_RUNTIME_DIR PULSE_SERVER XDG_DATA_HOME XDG_CONFIG_HOME TMPDIR WC_BRIDGE_PORT WC_HARNESS_PORT WCB_AUTOSTART $(compgen -e | grep '^VGL_'); do
    [ -n "${!v:-}" ] && E+=("$v=${!v}")
  done
  env -i "${E[@]}" \
    $RT ./Cities.x64 -screen-fullscreen 0 -screen-width 1280 -screen-height 800 -noWorkshop ${CS_ARGS:-} "$@"
  rc=$?; dt=$(( $(date +%s) - t0 ))
  [ "$dt" -ge "${CS_RETRY_WINDOW:-120}" ] && exit $rc
  echo "[cities] game exited with $rc after ${dt}s (try $try); retrying" >&2; sleep 5
done
exit $rc
