#!/usr/bin/env bash
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh; H=/tmp/gab-layer
# Xonotic 0.8.6, the upstream zip unpacked at the reference install path: binaries, data, server, misc,
# key_0.d0pk, COPYING; music packs and nexcompat left out. sha512 = https://dl.xonotic.org/xonotic-0.8.6.sha512.
# First-run dialogs: the XonStat Terms of Service is accepted for the benchmark runtime with
# statistics OFF (cl_allow_uid2name 0, cl_allow_uidtracking 0) and a
# player name set (without one the Welcome dialog appears). Seeded into the default userdir and kept as client/seed-config.cfg
# for the per-instance userdirs the adapter creates.
apt-get update && apt-get install -y --no-install-recommends libxpm4
R=/home/player/oss-xonotic; mkdir -p $R/client $R/inst $R/mods && cd $R
fetch https://dl.xonotic.org/xonotic-0.8.6.zip \
  cb39879e96f19abb2877588c2d50c5d3e64dd68153bec3dd1bebedf4d765e506afa419c28381d7005aed664cb1a042571c132b5b319e4308cab67745d996c2a6 x.zip
unzip -q x.zip "Xonotic/xonotic-linux64-*" "Xonotic/data/*" "Xonotic/server/*" "Xonotic/misc/*" "Xonotic/key_0.d0pk" "Xonotic/COPYING" -x "*music*" "*nexcompat*"
unzip -q x.zip "Xonotic/GPL-*" || true
rm -f x.zip
test -x Xonotic/xonotic-linux64-dedicated && test -f Xonotic/data/xonotic-20230620-maps.pk3
install -m 644 $H/seed-config.cfg $R/client/seed-config.cfg
mkdir -p /home/player/.xonotic/data && cp $H/seed-config.cfg /home/player/.xonotic/data/config.cfg
licences $R/Xonotic xonotic COPYING GPL-2 GPL-3
echo "Xonotic 0.8.6 -- source: https://dl.xonotic.org/xonotic-0.8.6-source.zip (GPL-3.0-or-later; DarkPlaces GPL-2.0-or-later)" > /opt/gab/licenses/xonotic/SOURCE.txt
chown -R player:player /home/player
echo "xonotic 0.8.6 $(date -u +%F)" >> /opt/gab/layers
