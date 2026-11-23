#!/usr/bin/env bash
# gab Cities: Skylines session runner, inside the Steam sandbox (written by the cities_skylines adapter into <root>/bin). Player sessions;
# the regression probes use csp.sh (the pool runner).
#   gcs.sh start <inst> <display>   private display (Xvfb 1280x800), the game through cs-start.sh with the instance's private user folder
#                                   (XDG_DATA_HOME / XDG_CONFIG_HOME / TMPDIR under <inst>) and private game folder; detached
#   gcs.sh stop <inst>              cs-start.sh first (it restarts a game that exits early), then the game, by exact PIDs
#   gcs.sh alive <inst>             "up" when its game runs
#   gcs.sh state <inst> [timeout] [dump]   one state request to the harness mod GABState -> its JSON answer (+ blocked: why
#                                   GABState did not start the city); dump: GABState also writes the stock prefab list
set -u
R=${CSP_ROOT:-/home/player/pool-probes/cities}
# one find / grep over /proc instead of a readlink / tr per process (2.5 s per scan on a busy host, 2026-11-05)
has_env() { tr '\0' '\n' < /proc/$1/environ 2>/dev/null | grep -qx "$2"; }
game_pids() { local p; for p in $(find /proc -maxdepth 2 -path '/proc/[0-9]*/exe' -lname '*/Cities.x64' 2>/dev/null | cut -d/ -f3); do has_env $p "CS_INSTANCE=gab-$1" && echo $p; done; }
starter_pids() { local f p; for f in $(grep -lzxF -- "$R/bin/cs-start.sh" /proc/[0-9]*/cmdline 2>/dev/null); do p=${f#/proc/}; p=${p%/cmdline}; case "$(tr '\0' ' ' < $f 2>/dev/null)" in "bash $R/bin/cs-start.sh"*) has_env $p "CS_INSTANCE=gab-$1" && echo $p;; esac; done; }
case "${1:-}" in
  start)
    I=$2; D=$3; N=${I##*/}
    [ -n "$(game_pids $N)" ] && { echo "already running"; exit 0; }
    [ -e /tmp/.X11-unix/X${D#:} ] || { rm -f /tmp/.X${D#:}-lock; setsid nohup Xvfb $D -screen 0 1280x800x24 -nolisten tcp > $I/xvfb.log 2>&1 < /dev/null & sleep 2; }
    mkdir -p /tmp/gabstate/$N; rm -f /tmp/gabstate/$N/req /tmp/gabstate/$N/ans /tmp/gabstate/$N/blocked
    ( cd $I/game || exit 1; exec env HOME=/home/player GAME_DIR=$I/game VGL=1 DISPLAY=$D XDG_DATA_HOME=$I/data XDG_CONFIG_HOME=$I/config TMPDIR=$I/tmp \
        XDG_RUNTIME_DIR=/tmp/xdg-runtime PULSE_SERVER=unix:/tmp/xdg-runtime/pulse/native CS_INSTANCE=gab-$N CS_RETRIES=2 \
        setsid nohup bash $R/bin/cs-start.sh ) > $I/game.out 2>&1 < /dev/null &
    echo $! > $I/start.pid   # the and-list form `cd && env ... &` left a subshell waiting on the game with the caller's stdout (2026-11-05)
    echo "started";;
  stop)
    I=$2; N=${I##*/}
    for p in $(starter_pids $N); do kill $p 2>/dev/null; done
    sleep 1; for p in $(game_pids $N); do kill -9 $p 2>/dev/null; done
    sleep 2; for p in $(game_pids $N); do kill -9 $p 2>/dev/null; done
    cp "$I/config/unity3d/Colossal Order/Cities_ Skylines/Player.log" $I/player.log 2>/dev/null; true;;
  alive) [ -n "$(game_pids ${2##*/})" ] && echo up || echo down;;
  state)
    I=$2; T=${3:-2.5}; N=${I##*/}; SD=/tmp/gabstate/$N; id=$(date +%s%N)${4:+:$4}; mkdir -p $SD; rm -f $SD/ans
    echo $id > $SD/req.tmp && mv -f $SD/req.tmp $SD/req
    python3 - "$SD" "${id%%:*}" "$T" "$(game_pids $N | head -1)" <<'PY'
import json, os, sys, time
d, rid, t, pid = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
end = time.time() + t
blocked = lambda: open(os.path.join(d, "blocked")).read()[:600] if os.path.exists(os.path.join(d, "blocked")) else None
while time.time() < end:
    try:
        a = json.load(open(os.path.join(d, "ans")))
        if str(a.get("id")) == rid:
            b = blocked()          # GABState keeps answering (status menu) when it refused to start past a dialog: say why
            print(json.dumps({**a, **({"blocked": b} if b else {})})); sys.exit(0)
    except (OSError, ValueError): pass
    time.sleep(0.03)
try: os.remove(os.path.join(d, "req"))
except OSError: pass
b = blocked()
print(json.dumps({"running": bool(pid), "error": "no answer from the game in %.1f s" % t, **({"blocked": b} if b else {})}))
PY
    ;;
  *) sed -n 2,9p "$0";;
esac
