#!/usr/bin/env bash
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh; H=/tmp/gab-layer
# Godot TPS demo (godotengine/tps-demo master at a82f154, "Update to Godot 4.7.2", 2026-08-31 -- the verified tree, from
# a tps-demo-master.tar of 2026-10-25) on the official Godot 4.7.2 Linux binary (sha512 from the release's
# SHA512-SUMS.txt). Layout as the adapter expects: /home/player/oss-godot-tps/{godot/,vanilla/ (+ .godot import cache)}.
# The import cache is built here (headless) so a trial never imports the 1 GB of assets. No EULA / ToS.
R=/home/player/oss-godot-tps; mkdir -p $R/godot $R/inst $R/mods $R/probe && cd $R/godot
fetch https://github.com/godotengine/godot/releases/download/4.7.2-stable/Godot_v4.7.2-stable_linux.x86_64.zip \
  9aa00f7a605200940bce3027a567b782f49bd8e940dd06ae9e987bd65aee1b1467edd56ed84fcdcbdd44354bf613bdbb4e5d2913e925850368e150c59ed54c65 godot.zip
unzip -q godot.zip && rm godot.zip && chmod +x Godot_v4.7.2-stable_linux.x86_64 && ln -sf Godot_v4.7.2-stable_linux.x86_64 godot
git_at https://github.com/godotengine/tps-demo.git a82f15448e9b015440d3bbdf5e10801b260c4e9f $R/vanilla
chown -R player:player /home/player
# first import as the player (headless, no GPU needed); a second pass settles resources that depend on others
for i in 1 2; do runuser -u player -- env HOME=/home/player timeout 3000 $R/godot/godot --headless --path $R/vanilla --import > /tmp/import$i.log 2>&1 || true; tail -3 /tmp/import$i.log; done
test -d $R/vanilla/.godot/imported && echo "import cache: $(du -sh $R/vanilla/.godot | cut -f1)"
licences $R/vanilla godot-tps LICENSE.md README.md
mkdir -p /opt/gab/licenses/godot && curl -fsSL -o /opt/gab/licenses/godot/LICENSE.txt https://raw.githubusercontent.com/godotengine/godot/4.7.2-stable/LICENSE.txt || true
curl -fsSL -o /opt/gab/licenses/godot/COPYRIGHT.txt https://raw.githubusercontent.com/godotengine/godot/4.7.2-stable/COPYRIGHT.txt || true
echo "tps-demo a82f15448e9b015440d3bbdf5e10801b260c4e9f -- https://github.com/godotengine/tps-demo ; Godot 4.7.2-stable -- https://github.com/godotengine/godot/releases/tag/4.7.2-stable" > /opt/gab/licenses/godot-tps/SOURCE.txt
rm -rf /tmp/import*.log; chown -R player:player /home/player
echo "godot-tps a82f154 godot 4.7.2 $(date -u +%F)" >> /opt/gab/layers
