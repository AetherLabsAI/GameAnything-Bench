#!/usr/bin/env bash
# GameAnything Valheim probe runner (inside the Steam sandbox). A private game folder per instance (small copies + a symlink to the
# Steam install's valheim_Data), its own BepInEx/plugins (ValheimBridge + the mods under test), its own save dir
# (VHBRIDGE_SAVEDIR), its own Unity log (-logFile), private bridge ports and the private Xvfb display. Never the lab's
# Valheim folder plugins, never the user's saves.
#   vhp.sh template                     (re)make game-tmpl/ from the Steam install (BepInEx core/config, doorstop, launcher)
#   vhp.sh run <inst> <mode> <groups>   start the game (boot-hang retries), run the driver (run1|run2), stop the game
set -u
own() { [ "$(readlink /proc/$1/ns/mnt 2>/dev/null)" = "$(readlink /proc/self/ns/mnt)" ]; }   # this container only: Steam trials share the hub's pid namespace
R=$(readlink -f ${VHP_ROOT:-/home/player/pool-probes/valheim})   # resolved: /proc/<pid>/exe shows real paths (HOME may be a symlink)
S=${VHP_STEAM:-/home/player/.steam/debian-installation/steamapps/common/Valheim}
BP=${VHP_BEPINEX:-$S}     # where BepInEx comes from: the Steam folder (the Steam install) or an unpacked BepInExPack_Valheim (gab)
DISP=${VHP_DISPLAY:-:151}
export DISPLAY=$DISP VHBRIDGE_PORT=6534 VHBRIDGE_HARNESS_PORT=6634 SteamAppId=892970 SteamGameId=892970
export XDG_RUNTIME_DIR=/tmp/xdg-runtime PULSE_SERVER=unix:/tmp/xdg-runtime/pulse/native PULSE_CLIENTCONFIG=/tmp/pulse-client-noshm.conf
[ -f /tmp/pulse-client-noshm.conf ] || echo "enable-shm = no" > /tmp/pulse-client-noshm.conf
game_pids() { local p e; for p in /proc/[0-9]*; do own ${p#/proc/} || continue; e=$(readlink "$p/exe" 2>/dev/null) || continue; case "$e" in "$R"/inst/*/game/valheim.x86_64|"$R"/gab/*/game/valheim.x86_64) echo "${p#/proc/}";; esac; done; }   # gab: instances under gab/ too (else stale games kept answering the harness port)
health() { python3 - "$VHBRIDGE_HARNESS_PORT" <<'PY'
import socket, sys
try: s = socket.create_connection(("127.0.0.1", int(sys.argv[1])), 2)
except OSError: print("refused"); sys.exit(1)
try:
    s.settimeout(5); s.sendall(b'{"jsonrpc":"2.0","id":1,"method":"health"}\n'); l = s.makefile().readline().strip()
    print(l if l else "hung"); sys.exit(0 if l else 1)
except OSError: print("hung"); sys.exit(1)
PY
}
stop_game() { local p; for p in $(game_pids); do kill -9 "$p" 2>/dev/null && echo "killed game pid $p"; done; sleep 2; }
case "$1" in
  template)
    T=$R/game-tmpl; rm -rf $T; mkdir -p $T
    cp -f $S/valheim.x86_64 $S/UnityPlayer.so $T/; cp -f $S/libdecor-0.so.0 $S/libdecor-cairo.so $S/steam_appid.txt $T/ 2>/dev/null
    [ -f $T/steam_appid.txt ] || echo 892970 > $T/steam_appid.txt
    if [ -f $BP/run_bepinex.sh ]; then cp -f $BP/run_bepinex.sh $T/; else cp -f $BP/start_game_bepinex.sh $T/run_bepinex.sh; fi; chmod +x $T/run_bepinex.sh
    cp -f $BP/doorstop_config.ini $T/; cp -f $BP/.doorstop_version $T/ 2>/dev/null; cp -rf $BP/doorstop_libs $T/
    mkdir -p $T/BepInEx/plugins; cp -rf $BP/BepInEx/core $T/BepInEx/; mkdir -p $T/BepInEx/config; cp -f $BP/BepInEx/config/BepInEx.cfg $T/BepInEx/config/ 2>/dev/null
    [ -f $S/BepInEx/plugins/ValheimBridge.dll ] && [ ! -f $R/ValheimBridge.dll ] && cp -f $S/BepInEx/plugins/ValheimBridge.dll $R/ValheimBridge.dll
    ln -sfn $S/valheim_Data $T/valheim_Data
    echo "template $T: $(du -sh --apparent-size $T | cut -f1)";;
  run)
    I=$(readlink -f $2); MODE=$3; GRP=${4:-}
    [ -z "$(game_pids)" ] || stop_game
    for try in 1 2 3; do
      echo "=== start try $try $(date -u +%T)" >> $I/game.out
      ( cd $I/game && VHBRIDGE_SAVEDIR=$I/save setsid nohup vglrun -d egl ./run_bepinex.sh ./valheim.x86_64 -force-glcore -screen-fullscreen 0 \
          -screen-width 1280 -screen-height 720 -console -logFile $I/player-$MODE.log ) >> $I/game.out 2>&1 < /dev/null &
      up=0; hung=0
      for _ in $(seq 1 120); do
        sleep 2
        if r=$(health); then up=1; break; fi
        [ -z "$(game_pids)" ] && sleep 3 && [ -z "$(game_pids)" ] && break
        if [ "$r" = hung ]; then hung=$((hung + 7)); [ $hung -ge 28 ] && break; else hung=0; fi
      done
      [ $up = 1 ] && break
      echo "boot failed (hung=$hung)" >> $I/game.out; stop_game >> $I/game.out
    done
    if [ $up = 1 ]; then
      ( cd $I && VHPROBE_OUT=$I/$MODE.jsonl timeout 1500 python3 $R/bin/driver.py $MODE "$GRP" ) > $I/$MODE.driver.out 2>&1
      echo "driver exit $?" >> $I/$MODE.driver.out
    else echo 'VHPROBE {"probe":"_fatal","error":"game did not boot (bridge never answered)"}' >> $I/$MODE.jsonl; fi
    stop_game >> $I/game.out
    cp $I/game/BepInEx/LogOutput.log $I/bepinex-$MODE.log 2>/dev/null
    echo "EXIT=$MODE" >> $I/$MODE.driver.out;;
  start)   # debugging: start the game for <inst> and leave it running (stop: vhp.sh stop)
    I=$(readlink -f $2); ( cd $I/game && VHBRIDGE_SAVEDIR=$I/save setsid nohup vglrun -d egl ./run_bepinex.sh ./valheim.x86_64 -force-glcore -screen-fullscreen 0 \
          -screen-width 1280 -screen-height 720 -console -logFile $I/player-dbg.log ) >> $I/game.out 2>&1 < /dev/null & echo started;;
  stop) stop_game;;
  shot) ffmpeg -loglevel error -y -f x11grab -video_size 1280x720 -i $DISP+0,0 -frames:v 1 "$2" && echo "$2";;
  *) sed -n 2,7p "$0";;
esac
