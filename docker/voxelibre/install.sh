#!/usr/bin/env bash
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh; H=/tmp/gab-layer
# VoxeLibre 0.92.4 on Luanti 5.17.0, laid out as the adapter expects (gab/game/voxelibre/probes.py INSTALL_SH): the official
# minetestdevs PPA .deb unpacked into /home/player/oss-voxelibre/root (+ its library dependencies from the pinned Ubuntu
# snapshot, installed system-wide), VoxeLibre at the commit of tag 0.92.4 in games/voxelibre. No EULA / ToS.
R=/home/player/oss-voxelibre; mkdir -p $R/debs $R/root $R/games $R/runs $R/bridge/vlprobe
fetch "https://ppa.launchpadcontent.net/minetestdevs/stable/ubuntu/pool/main/m/minetest/minetest_5.17.0-ppa0~ubuntu24.04.1_amd64.deb" \
      6692b0ebf5678d1b612b1ad75c2e2622f6717b9c461a1daebde49c5b713158cc $R/debs/luanti.deb
apt-get update
apt-get install -y --no-install-recommends $R/debs/luanti.deb     # pulls the dependencies (luajit, leveldb, snappy, ...) from the snapshot
dpkg -x $R/debs/luanti.deb $R/root
for p in libsnappy1v5 libleveldb1d libluajit-5.1-2 libluajit-5.1-common; do (cd $R/debs && apt-get download $p) && dpkg -x $R/debs/${p}_*.deb $R/root; done
LD_LIBRARY_PATH=$R/root/usr/lib/x86_64-linux-gnu $R/root/usr/bin/luanti --version | head -1
git_at https://git.minetest.land/VoxeLibre/VoxeLibre.git a5078bab2efbc8a670b8b10c290ade73aa87be6a $R/games/voxelibre refs/tags/0.92.4
grep '^version' $R/games/voxelibre/game.conf
licences $R/games/voxelibre voxelibre LICENSE.txt LEGAL.md CREDITS.md
licences $R/root/usr/share/doc minetest minetest
printf '%s\n' "VoxeLibre 0.92.4 (commit a5078bab) -- source: https://git.minetest.land/VoxeLibre/VoxeLibre/src/tag/0.92.4" \
  "Luanti 5.17.0 -- source: https://github.com/luanti-org/luanti/releases/tag/5.17.0 ; binary: Launchpad PPA minetestdevs/stable" > /opt/gab/licenses/voxelibre/SOURCE.txt
chown -R player:player /home/player
echo "voxelibre 0.92.4 luanti 5.17.0 $(date -u +%F)" >> /opt/gab/layers
