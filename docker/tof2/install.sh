#!/usr/bin/env bash
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh; H=/tmp/gab-layer
# Tanks of Freedom II (P1X-in/tanks-of-freedom-ii master at dbe629e, version 1.0.9; no binary release) on the official Godot
# 4.4.1 Linux binary (sha512 from SHA512-SUMS.txt). Layout as the adapter expects: /home/player/oss-tof2/{src (+ .godot import
# cache), godot/}. docs/ (marketing media under a proprietary licence, not needed at runtime) is left out; its CREDITS.md is kept
# with the licences. No EULA / ToS dialog; no network.
R=/home/player/oss-tof2; mkdir -p $R/godot $R/inst $R/bridge && cd $R/godot
fetch https://github.com/godotengine/godot/releases/download/4.4.1-stable/Godot_v4.4.1-stable_linux.x86_64.zip \
  ef4e76880a514257175544952c61191106fdef3095b909bafed9fcbeb230c3e5533920a0f3012882dd4bbde83028a67549825794e2d2c3cf76eba7918b71370e godot.zip
unzip -q godot.zip && rm godot.zip && chmod +x Godot_v4.4.1-stable_linux.x86_64 && ln -sf Godot_v4.4.1-stable_linux.x86_64 godot
git_at https://github.com/P1X-in/tanks-of-freedom-ii.git dbe629ea05691599dde4fd65ab9a13b78a4ccfe1 $R/src
licences $R/src tof2 LICENSE.md README.md docs/CREDITS.md
rm -rf $R/src/docs
chown -R player:player /home/player
for i in 1 2; do runuser -u player -- env HOME=/home/player timeout 3000 $R/godot/godot --headless --path $R/src --import > /tmp/import$i.log 2>&1 || true; tail -3 /tmp/import$i.log; done
test -d $R/src/.godot/imported && echo "import cache: $(du -sh $R/src/.godot | cut -f1)"
mkdir -p /opt/gab/licenses/godot && curl -fsSL -o /opt/gab/licenses/godot/LICENSE.txt https://raw.githubusercontent.com/godotengine/godot/4.4.1-stable/LICENSE.txt || true
echo "Tanks of Freedom II dbe629ea05691599dde4fd65ab9a13b78a4ccfe1 -- https://github.com/P1X-in/tanks-of-freedom-ii ; Godot 4.4.1-stable -- https://github.com/godotengine/godot/releases/tag/4.4.1-stable" > /opt/gab/licenses/tof2/SOURCE.txt
rm -f /tmp/import*.log; chown -R player:player /home/player
echo "tof2 dbe629e godot 4.4.1 $(date -u +%F)" >> /opt/gab/layers
