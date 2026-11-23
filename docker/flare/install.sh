#!/usr/bin/env bash
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh; H=/tmp/gab-layer
# Flare 1.15 (engine + fantasycore + empyrean_campaign): the upstream AppImage extracted to /home/player/oss-flare/squashfs-root
# (= gab/game/flare install()). sha256 = GitHub's asset digest. No EULA / ToS; fresh HOME per run (adapter).
R=/home/player/oss-flare; mkdir -p $R/dl $R/runs $R/bridge && cd $R
fetch https://github.com/flareteam/flare-game/releases/download/v1.15/flare-linux64-v1.15.AppImage \
      155cd3a58f0e510e5bbb9db1a093469c88e52c05295afef53c24854cc5bbaf82 dl/flare.AppImage
chmod +x dl/flare.AppImage && dl/flare.AppImage --appimage-extract > /dev/null && rm -f dl/flare.AppImage
test -x squashfs-root/AppRun && test -f squashfs-root/usr/share/flare/mods/empyrean_campaign/settings.txt
mkdir -p /opt/gab/licenses/flare
find squashfs-root -maxdepth 6 \( -iname 'COPYING*' -o -iname 'LICENSE*' -o -iname 'CREDITS*' -o -iname 'README*' \) -type f -exec cp --parents {} /opt/gab/licenses/flare/ \;
echo "Flare 1.15 -- source: https://github.com/flareteam/flare-engine/releases/tag/v1.15 and https://github.com/flareteam/flare-game/releases/tag/v1.15" > /opt/gab/licenses/flare/SOURCE.txt
chown -R player:player /home/player
echo "flare 1.15 $(date -u +%F)" >> /opt/gab/layers
