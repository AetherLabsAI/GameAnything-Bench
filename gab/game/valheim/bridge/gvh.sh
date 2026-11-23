#!/usr/bin/env bash
# gab Valheim session runner, inside the Steam sandbox (written by gab/game/valheim/adapter.py into <root>/bin). Player sessions of the
# benchmark; the regression probes use vhp.sh (the pool runner) + driver.py.
#   gvh.sh start <inst> <display> <harness port> <agent port> [console]   the game from <inst>/game (private folder: launcher copies,
#                         BepInEx/plugins = ValheimBridge + the mods), saves in <inst>/save (VHBRIDGE_SAVEDIR), its own Unity log,
#                         a private Xvfb display (1280x720) through VirtualGL; detached, pid file. `console` = the F5 console (dev only)
#   gvh.sh stop <inst>    kill the game by its exact pid (Unity hangs in its crash handler on SIGTERM: kill -9, as the labs do)
#   gvh.sh alive <inst>   "up" when it runs
#   gvh.sh rpc <port> <method> [params json] [timeout]   one JSON-RPC call to ValheimBridge -> its answer line
set -u
own() { [ "$(readlink /proc/$1/ns/mnt 2>/dev/null)" = "$(readlink /proc/self/ns/mnt)" ]; }   # this container only: Steam trials share the hub's pid namespace
R=${VHP_ROOT:-/home/player/pool-probes/valheim}
export SteamAppId=892970 SteamGameId=892970
export XDG_RUNTIME_DIR=/tmp/xdg-runtime PULSE_SERVER=unix:/tmp/xdg-runtime/pulse/native PULSE_CLIENTCONFIG=/tmp/pulse-client-noshm.conf
[ -f /tmp/pulse-client-noshm.conf ] || echo "enable-shm = no" > /tmp/pulse-client-noshm.conf 2>/dev/null
pid_of() { local p; p=$(cat "$1/game.pid" 2>/dev/null) || return 1; [ -n "$p" ] && kill -0 "$p" 2>/dev/null && [ "$(awk '{print $3}' /proc/$p/stat 2>/dev/null)" != Z ] && echo "$p"; }
case "${1:-}" in
  start)
    I=$(readlink -f $2); D=$3; HP=$4; AP=$5; CON=${6:-}
    [ -n "$(pid_of $I)" ] && { echo "already running"; exit 0; }
    if ! [ -e /tmp/.X11-unix/X${D#:} ]; then
      rm -f /tmp/.X${D#:}-lock; setsid nohup Xvfb $D -screen 0 1280x720x24 -nolisten tcp > $I/xvfb.log 2>&1 < /dev/null & echo $! > $I/xvfb.pid
      for _ in $(seq 1 40); do [ -e /tmp/.X11-unix/X${D#:} ] && break; sleep 0.25; done
    fi
    mkdir -p $I/save; A=(-force-glcore -screen-fullscreen 0 -screen-width 1280 -screen-height 720 -logFile $I/player.log); [ "$CON" = console ] && A+=(-console)
    ( cd $I/game && DISPLAY=$D VHBRIDGE_SAVEDIR=$I/save VHBRIDGE_PORT=$AP VHBRIDGE_HARNESS_PORT=$HP exec setsid nohup vglrun -d egl ./run_bepinex.sh ./valheim.x86_64 "${A[@]}" ) \
      > $I/game.out 2>&1 < /dev/null &
    sleep 1
    # run_bepinex.sh ends in exec of the game: the pid of the launcher shell becomes the game's
    for p in /proc/[0-9]*; do own ${p#/proc/} || continue; case "$(readlink $p/exe 2>/dev/null)" in "$I/game/valheim.x86_64") echo ${p#/proc/} > $I/game.pid;; esac; done
    [ -s $I/game.pid ] || echo $! > $I/game.pid
    echo "started $(cat $I/game.pid)";;
  stop)
    I=$(readlink -f $2); for p in /proc/[0-9]*; do own ${p#/proc/} || continue; case "$(readlink $p/exe 2>/dev/null)" in "$I/game/valheim.x86_64") kill -9 ${p#/proc/} 2>/dev/null && echo "killed ${p#/proc/}";; esac; done
    p=$(pid_of $I) && kill -9 $p 2>/dev/null; rm -f $I/game.pid; cp $I/game/BepInEx/LogOutput.log $I/bepinex.log 2>/dev/null; true;;
  alive) I=$(readlink -f $2); for p in /proc/[0-9]*; do own ${p#/proc/} || continue; case "$(readlink $p/exe 2>/dev/null)" in "$I/game/valheim.x86_64") [ "$(awk '{print $3}' $p/stat 2>/dev/null)" != Z ] && { echo up; exit 0; };; esac; done; echo down;;
  rpc)
    python3 - "$2" "$3" "${4:-{\}}" "${5:-30}" <<'PY'
import json, socket, sys
port, method, params, t = int(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3]), float(sys.argv[4])
try:
    with socket.create_connection(("127.0.0.1", port), 3) as s:
        s.settimeout(t)
        s.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) + "\n").encode())
        print(s.makefile(encoding="utf-8").readline().strip() or json.dumps({"error": {"message": "no reply"}}))
except OSError as e: print(json.dumps({"error": {"message": f"bridge not reachable: {e}"}}))
PY
    ;;
  *) sed -n 2,11p "$0";;
esac
