#!/usr/bin/env bash
# gab Terraria (tModLoader) session runner, inside the Steam sandbox (written by gab/game/terraria/adapter.py into <root>/bin).
# Player sessions of the benchmark; the regression probes use trp.sh (the pool runner, unchanged apart from its root).
#   gtr.sh world <inst> <player> <world> <seed> <size>   headless server with the instance's mods: create the world (-autocreate,
#                                                         unless <inst>/save/Worlds/<world>.wld exists: then it is loaded) and the
#                                                         start player (GABState, like the character creation screen); then stop
#   gtr.sh start <inst> <display> <menu|game> <player> <world>   the client on its own Xvfb display (1280x720) through VirtualGL,
#                                                         GABState answering state requests in <inst>/state; detached, pid file
#   gtr.sh stop <inst>                                    stop the client (its process group), keep its client.log in <inst>
#   gtr.sh alive <inst>                                   "up" when the client runs
#   gtr.sh state <inst> [timeout_s]                       one state request -> the JSON answer (or {"running": .., "error": ..})
#   gtr.sh shot <display> <out.png>                       a screenshot of the display
# Root: $TRP_ROOT (default /home/player/pool-probes/terraria): steamapps/common/tModLoader = the private copy of the Steam install.
set -u
own() { [ "$(readlink /proc/$1/ns/mnt 2>/dev/null)" = "$(readlink /proc/self/ns/mnt)" ]; }   # this container only: Steam trials share the hub's pid namespace
R=${TRP_ROOT:-/home/player/pool-probes/terraria}
G=$R/steamapps/common/tModLoader
DN=$( [ -x "$G/dotnet/dotnet" ] && echo "$G/dotnet/dotnet" || command -v dotnet )
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 DOTNET_gcServer=0 SteamAppId=1281930 SteamGameId=1281930
export XDG_RUNTIME_DIR=/tmp/xdg-runtime PULSE_SERVER=unix:/tmp/xdg-runtime/pulse/native PULSE_CLIENTCONFIG=/tmp/pulse-client-noshm.conf
mkdir -p -m 700 /tmp/xdg-runtime 2>/dev/null
# no PulseAudio (docker trial): SDL's dummy driver allows ONE open device and FNA opens two ("Audio device already open",
# engine crash); ALSA's null device (the image's /etc/asound.conf) takes any number
[ -S /tmp/xdg-runtime/pulse/native ] || export SDL_AUDIODRIVER=alsa
# ... and that device must be LISTED (SDL counts ALSA's hinted devices; none -> FAudio's NoAudioHardware -> tModLoader's blocking
# "No audio hardware found" dialog): a hinted null pcm in the game user's ~/.asoundrc
if ! [ -S /tmp/xdg-runtime/pulse/native ] && ! grep -q gab-null ~/.asoundrc 2>/dev/null; then
  printf 'pcm.!default { type null hint { show on description "gab-null silent output" } }\npcm.gabnull { type null hint { show on description "gab-null silent output" } }\n' > ~/.asoundrc
fi
[ -f /tmp/pulse-client-noshm.conf ] || echo "enable-shm = no" > /tmp/pulse-client-noshm.conf 2>/dev/null
cfg() {  # no first-run screens (language picker, tModLoader's Welcome / what's new), a 1280x720 window, autosave off
  python3 - "$1/config.json" "$G/tModLoader.dll" <<'PY'
import json, re, sys
f, dll = sys.argv[1:]
try: c = json.load(open(f, encoding="utf-8-sig"))
except (OSError, ValueError): c = {}
ver = None
try:
    m = re.search("ProductVersion".encode("utf-16-le") + rb"\x00\x00((?:[^\x00]\x00)+)", open(dll, "rb").read())
    v = re.search(r"\+(\d+(?:\.\d+){1,3})", m.group(1).decode("utf-16-le")) if m else None
    ver = ".".join(str(int(x)) for x in v.group(1).split(".")) if v else None
except OSError: pass
c.update({"Language": "en-US", "LastLaunchedVersion": 279, "Fullscreen": False, "WindowMaximized": False, "DisplayWidth": 1280,
          "DisplayHeight": 720, "Autosave": False})
if ver: c["LastLaunchedTModLoaderVersion"] = ver
open(f, "w", encoding="utf-8").write(json.dumps(c, indent=2))
PY
}
sdir() { echo /tmp/gabstate/$(basename "$1"); }   # the request / answer files on local disk (the instance may sit on a network disk)
pid_of() { local p; p=$(cat "$1/client.pid" 2>/dev/null) || return 1; [ -n "$p" ] && kill -0 "$p" 2>/dev/null && [ "$(awk '{print $3}' /proc/$p/stat 2>/dev/null)" != Z ] && echo "$p"; }
case "${1:-}" in
  world)
    I=$2; P=$3; W=$4; SEED=$5; SIZE=${6:-1}; mkdir -p $I/save/Worlds $I/save/Players; cfg $I/save; rm -f $I/world.out
    ( cd $G && GABSTATE_NEWPLAYER="$P" GABSTATE_OUT=$I/world.out timeout -k 5 420 "$DN" tModLoader.dll -server -tmlsavedirectory $I/save \
        -autocreate "$SIZE" -seed "$SEED" -world "$I/save/Worlds/$W.wld" -worldname "$W" -players 1 -port 7797 -noupnp < /dev/null > $I/server.out 2>&1 ) &
    SP=$!
    for _ in $(seq 1 400); do sleep 1; grep -q '^GABSTATE ' $I/world.out 2>/dev/null && break; kill -0 $SP 2>/dev/null || break; done
    sleep 1
    for p in /proc/[0-9]*; do own ${p#/proc/} || continue; a=$(tr '\0' ' ' < $p/cmdline 2>/dev/null); case "$a" in *"tModLoader.dll -server -tmlsavedirectory $I/save "*) kill ${p#/proc/};; esac; done
    wait $SP 2>/dev/null; cp $G/tModLoader-Logs/server.log $I/server.log 2>/dev/null
    if grep -q '^GABSTATE done' $I/world.out 2>/dev/null; then echo "WORLD OK $(cat $I/world.out)"; else echo "WORLD FAILED"; tail -c 1500 $I/world.out 2>/dev/null; fi;;
  start)
    I=$2; D=$3; MODE=$4; P=$5; W=$6
    [ -n "$(pid_of $I)" ] && { echo "already running"; exit 0; }
    if ! [ -e /tmp/.X11-unix/X${D#:} ]; then
      rm -f /tmp/.X${D#:}-lock; setsid nohup Xvfb $D -screen 0 1280x720x24 -nolisten tcp > $I/xvfb.log 2>&1 < /dev/null & echo $! > $I/xvfb.pid
      for _ in $(seq 1 40); do [ -e /tmp/.X11-unix/X${D#:} ] && break; sleep 0.25; done
    fi
    cfg $I/save; SD=$(sdir $I); rm -rf $SD; mkdir -p $SD
    SEL=(); [ "$MODE" = game ] && SEL=(-skipselect "$P:$W")
    ( cd $G && GABSTATE_DIR=$SD DISPLAY=$D exec setsid nohup vglrun -d egl "$DN" tModLoader.dll -tmlsavedirectory $I/save "${SEL[@]}" ) > $I/client.out 2>&1 < /dev/null &
    echo $! > $I/client.pid; echo "started $(cat $I/client.pid)";;
  stop)
    I=$2; p=$(pid_of $I) && { kill -- -$p 2>/dev/null; kill $p 2>/dev/null; for _ in $(seq 1 30); do kill -0 $p 2>/dev/null || break; sleep 0.5; done; kill -9 -- -$p 2>/dev/null; kill -9 $p 2>/dev/null; }
    rm -f $I/client.pid; cp $G/tModLoader-Logs/client.log $I/client.log 2>/dev/null; true;;
  alive) [ -n "$(pid_of $2)" ] && echo up || echo down;;
  state)
    I=$2; T=${3:-2.5}; SD=$(sdir $I); id=$(date +%s%N); mkdir -p $SD; rm -f $SD/ans; echo $id > $SD/req.tmp && mv -f $SD/req.tmp $SD/req
    python3 - "$SD" "$id" "$T" "$(pid_of $I)" <<'PY'
import json, os, sys, time
d, rid, t, pid = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
end = time.time() + t
while time.time() < end:
    try:
        a = json.load(open(os.path.join(d, "ans")))
        if str(a.get("id")) == rid: print(json.dumps(a)); sys.exit(0)
    except (OSError, ValueError): pass
    time.sleep(0.03)
try: os.remove(os.path.join(d, "req"))
except OSError: pass
print(json.dumps({"running": bool(pid), "error": "no answer from the game in %.1f s" % t}))
PY
    ;;
  shot) ffmpeg -loglevel error -y -f x11grab -video_size 1280x720 -i $2+0,0 -frames:v 1 "$3" && echo "$3";;
  *) sed -n 2,12p "$0";;
esac
