#!/usr/bin/env bash
# GameAnything Cities: Skylines probe runner (inside the Steam sandbox; gab copy of the 2026-10-30 pool runner). Private user folder
# per instance (XDG_DATA_HOME, XDG_CONFIG_HOME, TMPDIR under the instance), the start script cs-start.sh (= the lab's
# hosting/container/cities/start-game.sh: VirtualGL, Steam client env), a private 1280x800 display. Never the user's own Colossal
# Order folder (only colossal.cgs / gameSettings.cgs are copied from it, read only). gab changes: root / game folder / display /
# start script are parameters; the private game folder's colossal.cgs is a COPY of the Steam folder's (where the game saved the
# Paradox legal acceptance the user clicked herself), only TelemetryConsent is set to false; no legal key is written here.
#   csp.sh display                                     the private display (Xvfb 1280x800) if it is not up
#   csp.sh prep <inst> <mode> <groups> [mod dirs...]   user folder, CSProbe + mods under test, switches, probe.cfg (env CSP_START,
#                                                      CSP_FIXTURE, CSP_TURBO: optional probe.cfg switches)
#   csp.sh run <inst> <mode>                           start, wait for _done/_fatal (max 30 min), stop, copy Player.log
set -u
R=${CSP_ROOT:-/home/player/pool-probes/cities}
GD=${CSP_GAME:-/home/player/.steam/debian-installation/steamapps/common/Cities_Skylines}
REAL="/home/player/.local/share/Colossal Order/Cities_Skylines"
DISP=${CSP_DISPLAY:-:158}
# one find / grep over /proc instead of a readlink / tr per process (2.5 s per scan on a busy host, 2026-11-05)
has_env() { tr '\0' '\n' < /proc/$1/environ 2>/dev/null | grep -qx "$2"; }
game_pids() { local p; for p in $(find /proc -maxdepth 2 -path '/proc/[0-9]*/exe' -lname '*/Cities.x64' 2>/dev/null | cut -d/ -f3); do has_env $p "CS_INSTANCE=gaprobe-$1" && echo $p; done; }
starter_pids() { local f p; for f in $(grep -lzxF -- "$R/bin/cs-start.sh" /proc/[0-9]*/cmdline 2>/dev/null); do p=${f#/proc/}; p=${p%/cmdline}; case "$(tr '\0' ' ' < $f 2>/dev/null)" in "bash $R/bin/cs-start.sh"*) has_env $p "CS_INSTANCE=gaprobe-$1" && echo $p;; esac; done; }
case "$1" in
  prep)
    I=$2; MODE=$3; GRP=$4; shift 4
    U="$I/data/Colossal Order/Cities_Skylines"; mkdir -p "$U/Addons/Mods/CSProbe" "$I/config" "$I/tmp"
    [ -f "$U/colossal.cgs" ] || cp "$REAL/colossal.cgs" "$U/" 2>/dev/null; [ -f "$U/gameSettings.cgs" ] || cp "$REAL/gameSettings.cgs" "$U/" 2>/dev/null
    cp $R/CSProbe.dll "$U/Addons/Mods/CSProbe/"
    printf '%s\n%s\n%s\n' "$MODE" "$GRP" "$I/$MODE.jsonl" > "$U/Addons/Mods/CSProbe/probe.cfg"
    # optional switches (2026-11-05): CSP_START (start=new:<map> | load:<save>), CSP_FIXTURE (the probe-city fixture folder: its save
    # goes into Saves/, its fixture.cfg is named to CSProbe), CSP_TURBO (0 | 1)
    { [ -n "${CSP_START:-}" ] && echo "start=$CSP_START"; [ -n "${CSP_FIXTURE:-}" ] && echo "fixture=$CSP_FIXTURE/fixture.cfg"
      [ -n "${CSP_TURBO:-}" ] && echo "turbo=$CSP_TURBO"; true; } >> "$U/Addons/Mods/CSProbe/probe.cfg"
    [ -n "${CSP_FIXTURE:-}" ] && { mkdir -p "$U/Saves" && cp "$CSP_FIXTURE"/gafixture*.crp "$U/Saves/" || { echo "no fixture save in $CSP_FIXTURE"; exit 1; }; }
    dirs=("$U/Addons/Mods/CSProbe")
    for m in "$@"; do cp -r "$m" "$U/Addons/Mods/"; dirs+=("$U/Addons/Mods/$(basename "$m")"); done
    python3 $R/bin/cgs.py "$U/userGameState.cgs" --enable "${dirs[@]}" > /dev/null
    # a private game folder: the game reads its "colossal" system settings (legal acceptance, telemetry) from its executable
    # folder, which in the Steam install is root-owned (never saved there). Cities.x64 copied, everything else symlinked.
    mkdir -p $I/game; cp -f $GD/Cities.x64 $I/game/; for e in Cities_Data Files Mono LauncherAssets Tools steamapps launcher-settings.json; do ln -sfn $GD/$e $I/game/$e; done
    rm -f $I/game/colossal.cgs; [ -f $GD/colossal.cgs ] && cp $GD/colossal.cgs $I/game/colossal.cgs
    python3 - "$I/game/colossal.cgs" "$R/bin" <<'PY'
import sys; sys.path.insert(0, sys.argv[2]); import cgs
from pathlib import Path
p = Path(sys.argv[1]); d = cgs.read(p) if p.exists() else {"_version": 4, **{k: {} for k in cgs.KINDS}}
d["bool"]["TelemetryConsent"] = False   # privacy-preserving default (Paradox optional telemetry off)
cgs.write(p, d)
PY
    echo "prepared $I ($MODE: $GRP; mods: ${dirs[*]})";;
  run)
    I=$2; MODE=$3; N=${I##*/}
    [ -n "$(game_pids $N)" ] && { echo "already running"; exit 1; }
    ( cd $I/game || exit 1; exec env HOME=/home/player GAME_DIR=$I/game VGL=1 DISPLAY=$DISP XDG_DATA_HOME=$I/data XDG_CONFIG_HOME=$I/config TMPDIR=$I/tmp \
        XDG_RUNTIME_DIR=/tmp/xdg-runtime PULSE_SERVER=unix:/tmp/xdg-runtime/pulse/native CS_INSTANCE=gaprobe-$N CS_RETRIES=2 \
        setsid nohup bash $R/bin/cs-start.sh ) > $I/game-$MODE.out 2>&1 < /dev/null &
    echo $! > $I/start-$MODE.pid   # the and-list form `cd && env ... &` left a subshell waiting on the game with the caller's stdout (2026-11-05)
    t0=$(date +%s)
    while [ $(( $(date +%s) - t0 )) -lt ${CSP_TIMEOUT:-1800} ]; do
      sleep 5
      grep -qE '"probe":"(_done|_fatal)"' $I/$MODE.jsonl 2>/dev/null && { sleep 8; break; }
      [ $(( $(date +%s) - t0 )) -gt 60 ] && [ -z "$(game_pids $N)" ] && { sleep 10; [ -z "$(game_pids $N)" ] && break; }
    done
    # start-game.sh first (it restarts a game that exits within 2 min), then the game: exact PIDs carrying our CS_INSTANCE
    for p in $(starter_pids $N); do kill $p 2>/dev/null && echo "stopped start-game.sh $p"; done
    sleep 1
    for p in $(game_pids $N); do kill -9 $p 2>/dev/null && echo "killed game $p"; done
    sleep 3; for p in $(game_pids $N); do kill -9 $p 2>/dev/null && echo "killed late game $p"; done
    cp "$I/config/unity3d/Colossal Order/Cities_ Skylines/Player.log" $I/player-$MODE.log 2>/dev/null
    echo "EXIT=$MODE $(( $(date +%s) - t0 ))s" >> $I/$MODE.runner.out;;
  display) [ -e /tmp/.X11-unix/X${DISP#:} ] || { rm -f /tmp/.X${DISP#:}-lock; setsid nohup Xvfb $DISP -screen 0 1280x800x24 -nolisten tcp > $R/xvfb.log 2>&1 < /dev/null & sleep 2; }; echo "display $DISP";;
  shot) ffmpeg -loglevel error -y -f x11grab -video_size 1280x800 -i $DISP+0,0 -frames:v 1 "$2" && echo "$2";;
esac
