#!/usr/bin/env bash
# Image smoke test (no gab, no adapter): start the game's image the way the docker backend does, launch the game on the seat
# screen, take a screenshot after WAIT seconds, copy it out with the game's log, remove the container.
#   docker/smoke.sh <game>... | all        OUT=dir (default ./gab-smoke)  WAIT=40  GPU=1 (adds --gpus all)  IMAGE=<ref> (one game)
#   images: docker/images.yaml (pinned digest; TAG=<tag> uses <repo>:<tag> instead)
set -uo pipefail
cd "$(dirname "$0")"
OUT=${OUT:-./gab-smoke}; WAIT=${WAIT:-40}; mkdir -p "$OUT"
GAMES=("$@"); [ "${1:-}" = all ] && GAMES=(voxelibre xonotic godot-tps flare tuxemon tof2 colobot celeste64 supertuxkart)
[ ${#GAMES[@]} -gt 0 ] || { echo "usage: docker/smoke.sh <game>... | all" >&2; exit 2; }
ref() { python3 - "$1" <<'PY'
import os, sys, yaml
m = yaml.safe_load(open("images.yaml")); g = m["games"][sys.argv[1]]; repo = f"{m['registry']}/{g['repo']}"
t = os.environ.get("TAG"); print(f"{repo}:{t}" if t else f"{repo}@{g['digest']}" if g.get("digest") else f"{repo}:{m['tag']}")
PY
}
launch() { case $1 in   # the game's own start, software GL unless GPU=1 (then vglrun uses the GPU), 1024x768 where it can be set
  voxelibre) echo 'cd /home/player/oss-voxelibre && mkdir -p /tmp/h && HOME=/tmp/h LD_LIBRARY_PATH=root/usr/lib/x86_64-linux-gnu vglrun -d egl root/usr/bin/luanti';;
  xonotic) echo 'U=/tmp/xu; mkdir -p $U/data && cp /home/player/oss-xonotic/client/seed-config.cfg $U/data/config.cfg && cd /home/player/oss-xonotic/Xonotic && vglrun -d egl ./xonotic-linux64-glx -userdir $U -nosound +vid_fullscreen 0 +vid_width 1024 +vid_height 768';;
  godot-tps) echo 'cd /home/player/oss-godot-tps && XDG_DATA_HOME=/tmp/gd vglrun -d egl godot/godot --path vanilla --rendering-method gl_compatibility --rendering-driver opengl3 --resolution 1024x768 --position 0,0';;
  flare) echo 'mkdir -p /tmp/h && HOME=/tmp/h /home/player/oss-flare/squashfs-root/AppRun';;
  tuxemon) echo 'cd /home/player/oss-tuxemon/src && HOME=/tmp/h ../venv/bin/python run_tuxemon.py';;
  tof2) echo 'cd /home/player/oss-tof2 && XDG_DATA_HOME=/tmp/gd godot/godot --path src --rendering-method gl_compatibility --rendering-driver opengl3 --resolution 1024x768 --position 0,0';;
  colobot) echo 'LIBGL_ALWAYS_SOFTWARE=1 /home/player/oss-colobot/run.sh /tmp/colobot-save';;
  celeste64) echo 'cd /home/player/oss-celeste64/build-plain && ./Celeste64';;
  supertuxkart) echo 'cd /home/player/oss-supertuxkart/SuperTuxKart-1.5-linux-x86_64 && vglrun -d egl ./run_game.sh --windowed --screensize=1024x768';;
  *) echo "unknown game $1" >&2; return 1;;
esac; }
rc=0
for g in "${GAMES[@]}"; do
  img=${IMAGE:-$(ref $g)}; c=gab-smoke-$g-$$; gpu=(); [ "${GPU:-0}" = 1 ] && gpu=(--gpus all)
  echo "== $g  $img"
  docker run -d --name $c --init --network none --shm-size 1g --pids-limit 8192 --security-opt no-new-privileges "${gpu[@]}" \
    -e GAB_GPU=${GPU:-0} "$img" > /dev/null || { echo "   FAIL docker run"; rc=1; continue; }
  for i in $(seq 1 30); do docker exec $c xdpyinfo -display :101 >/dev/null 2>&1 && break; sleep 1; done
  docker exec $c id
  docker exec -d $c bash -c "$(launch $g) > /tmp/game.log 2>&1"
  sleep "$WAIT"
  alive=$(docker exec $c bash -c 'ps -eo user=,comm= | grep -v -E "Xvfb|gab-seat|sleep|bash|ps|grep|docker-init|tini" | head -5' | tr '\n' ' ')
  docker exec $c ffmpeg -loglevel error -y -f x11grab -video_size 1024x768 -i :101 -frames:v 1 /tmp/shot.png
  docker cp $c:/tmp/shot.png "$OUT/$g.png" >/dev/null 2>&1 && echo "   screenshot $OUT/$g.png" || { echo "   FAIL screenshot"; rc=1; }
  docker exec $c tail -40 /tmp/game.log > "$OUT/$g.log" 2>&1
  echo "   processes: ${alive:-NONE (the game exited: see $OUT/$g.log)}"
  docker rm -f -v $c > /dev/null
done
exit $rc
