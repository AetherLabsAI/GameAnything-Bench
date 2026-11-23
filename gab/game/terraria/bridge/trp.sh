#!/usr/bin/env bash
# GameAnything Terraria probe runner (inside the Steam sandbox). Private tModLoader copy + private Xvfb display; never another user's files.
#   trp.sh build <src dir> <out.tmod dir>          build a tModLoader mod source (folder with build.txt) into <dir>/<Name>.tmod
#   trp.sh server <inst> <groups>                  headless server: autocreate the seeded world in <inst>/save, run groups, exit
#   trp.sh client <inst> <groups> <tag>            client into GAProbe:Probe on the private display, run groups, exit
#   trp.sh display                                 start the private Xvfb (:151) if it is not running
set -u
own() { [ "$(readlink /proc/$1/ns/mnt 2>/dev/null)" = "$(readlink /proc/self/ns/mnt)" ]; }   # this container only: Steam trials share the hub's pid namespace
R=${TRP_ROOT:-/home/player/pool-probes/terraria}
G=$R/steamapps/common/tModLoader
DN=$( [ -x "$G/dotnet/dotnet" ] && echo "$G/dotnet/dotnet" || command -v dotnet )   # tModLoader's own runtime, else the system .NET 8
DISP=${TRP_DISPLAY:-:151}
SEED=${TRP_SEED:-gaprobe1}
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 DOTNET_gcServer=0 SteamAppId=1281930 SteamGameId=1281930 XDG_RUNTIME_DIR=/tmp/xdg-runtime PULSE_SERVER=unix:/tmp/xdg-runtime/pulse/native PULSE_CLIENTCONFIG=/tmp/pulse-client-noshm.conf
mkdir -p -m 700 /tmp/xdg-runtime 2>/dev/null
# no PulseAudio (docker trial): SDL's dummy driver allows ONE open device and FNA opens two ("Audio device already open",
# engine crash); ALSA's null device (the image's /etc/asound.conf) takes any number
[ -S /tmp/xdg-runtime/pulse/native ] || export SDL_AUDIODRIVER=alsa
# ... and that device must be LISTED (SDL counts ALSA's hinted devices; none -> FAudio's NoAudioHardware -> tModLoader's blocking
# "No audio hardware found" dialog): a hinted null pcm in the game user's ~/.asoundrc
if ! [ -S /tmp/xdg-runtime/pulse/native ] && ! grep -q gab-null ~/.asoundrc 2>/dev/null; then
  printf 'pcm.!default { type null hint { show on description "gab-null silent output" } }\npcm.gabnull { type null hint { show on description "gab-null silent output" } }\n' > ~/.asoundrc
fi
cfg() {  # no first-run screens, a 1280x720 window
  python3 - "$1/config.json" <<'PY'
import json, sys
f = sys.argv[1]
try: c = json.load(open(f, encoding="utf-8-sig"))
except (OSError, ValueError): c = {}
c.update({"Language": "en-US", "LastLaunchedVersion": 279, "LastLaunchedTModLoaderVersion": "2026.8.3.0", "Fullscreen": False, "WindowMaximized": False,
          "DisplayWidth": 1280, "DisplayHeight": 720, "Autosave": False, "UseSmartCursorForCommonBlocks": False})
open(f, "w", encoding="utf-8").write(json.dumps(c, indent=2))
PY
}
case "$1" in
  display)
    if ! [ -e /tmp/.X11-unix/X${DISP#:} ]; then setsid nohup Xvfb $DISP -screen 0 1280x720x24 -nolisten tcp > $R/xvfb.log 2>&1 < /dev/null & echo $! > $R/xvfb.pid; sleep 2; fi
    echo "xvfb pid $(cat $R/xvfb.pid 2>/dev/null)";;
  build)
    SRC=$(readlink -f "$2"); N=$(basename "$SRC"); OUT=$3; B=$R/build/ModSources; mkdir -p $B $OUT $R/build/save/Mods
    rm -rf "$B/$N"; cp -r "$SRC" "$B/$N"; rm -rf "$B/$N/bin" "$B/$N/obj"
    [ -f "$B/$N/$N.csproj" ] || printf '<Project Sdk="Microsoft.NET.Sdk">\n  <Import Project="..\\tModLoader.targets" />\n  <PropertyGroup><AssemblyName>%s</AssemblyName></PropertyGroup>\n</Project>\n' "$N" > "$B/$N/$N.csproj"
    printf '<Project ToolsVersion="14.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">\n  <PropertyGroup><ExtraBuildModFlags>-tmlsavedirectory "%s"</ExtraBuildModFlags></PropertyGroup>\n  <Import Project="%s/tMLMod.targets" />\n</Project>\n' "$R/build/save" "$G" > $B/tModLoader.targets
    t0=$(date +%s)
    ( cd "$B/$N" && dotnet build "$N.csproj" -c Release -nologo -clp:NoSummary ) > $R/build/$N.log 2>&1
    if [ ! -f $R/build/save/Mods/$N.tmod ] || [ $(stat -c %Y $R/build/save/Mods/$N.tmod) -lt $t0 ]; then grep -E ' error |error CS' $R/build/$N.log | sed "s#$B/##" | sort -u | head -40; echo BUILD FAILED; exit 1; fi
    cp $R/build/save/Mods/$N.tmod $OUT/ && echo "built $OUT/$N.tmod";;
  server)
    I=$2; mkdir -p $I/save/Worlds; cfg $I/save
    ( cd $G && GAPROBE=$3 GAPROBE_OUT=$I/server.jsonl timeout -k 5 300 "$DN" tModLoader.dll -server -tmlsavedirectory $I/save -autocreate 1 -seed $SEED \
        -world $I/save/Worlds/Probe.wld -worldname Probe -players 1 -port 7799 -noupnp < /dev/null > $I/server.out 2>&1 ) &
    SP=$!
    for _ in $(seq 1 280); do sleep 1; grep -q '"probe":"_done"' $I/server.jsonl 2>/dev/null && break; kill -0 $SP 2>/dev/null || break; done
    sleep 2
    for p in /proc/[0-9]*; do own ${p#/proc/} || continue; a=$(tr '\0' ' ' < $p/cmdline 2>/dev/null); case "$a" in *"tModLoader.dll -server -tmlsavedirectory $I/save "*) kill ${p#/proc/};; esac; done
    wait $SP 2>/dev/null; cp $G/tModLoader-Logs/server.log $I/server.log 2>/dev/null; echo "EXIT=server" >> $I/server.out;;
  client)
    I=$2; TAG=$4; cfg $I/save
    ( cd $G && GAPROBE=$3 GAPROBE_OUT=$I/$TAG.jsonl DISPLAY=$DISP timeout -k 10 ${TRP_TIMEOUT:-600} vglrun -d egl "$DN" tModLoader.dll -tmlsavedirectory $I/save -skipselect "GAProbe:Probe" < /dev/null > $I/$TAG.out 2>&1; echo "EXIT=$?" >> $I/$TAG.out
      cp $G/tModLoader-Logs/client.log $I/$TAG.client.log 2>/dev/null )
    ;;
  shot) ffmpeg -loglevel error -y -f x11grab -video_size 1280x720 -i $DISP+0,0 -frames:v 1 "$2" && echo "$2";;
  *) sed -n 2,7p "$0";;
esac
