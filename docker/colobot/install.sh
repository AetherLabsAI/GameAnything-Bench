#!/usr/bin/env bash
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh; H=/tmp/gab-layer
# Colobot: Gold Edition 0.2.1-alpha = the Ubuntu noble packages 0.2.1-1build3 (universe; apt checks them against the signed
# snapshot index). Layout as the adapter expects: the four colobot packages unpacked into /home/player/oss-colobot/root + run.sh;
# their library dependencies installed system-wide. Mesa software rendering (the GPU path was found too slow).
# No EULA / ToS dialog.
R=/home/player/oss-colobot; mkdir -p $R/debs $R/root $R/inst && cd $R/debs
apt-get update
V=0.2.1-1build3
apt-get install -y --no-install-recommends colobot=$V colobot-common=$V colobot-common-sounds=$V colobot-common-textures=$V
apt-get download colobot=$V colobot-common=$V colobot-common-sounds=$V colobot-common-textures=$V
for d in colobot*.deb; do [ -f "$d" ] && dpkg -x "$d" $R/root; done
apt-get remove -y colobot colobot-common colobot-common-sounds colobot-common-textures   # keep only the dependencies system-wide
test -x $R/root/usr/games/colobot
mkdir -p $R/root/usr/share/fonts/truetype/dejavu
for f in /usr/share/fonts/truetype/dejavu/*.ttf; do [ -e $R/root/usr/share/fonts/truetype/dejavu/$(basename $f) ] || ln -s $f $R/root/usr/share/fonts/truetype/dejavu/; done
install -m 755 $H/run.sh $R/run.sh
licences $R/root/usr/share/doc colobot colobot colobot-common
echo "Colobot 0.2.1 (Ubuntu noble 0.2.1-1build3) -- source: apt-get source colobot / https://launchpad.net/ubuntu/+source/colobot/0.2.1-1build3 ; upstream https://github.com/colobot/colobot" > /opt/gab/licenses/colobot/SOURCE.txt
chown -R player:player /home/player
echo "colobot 0.2.1-1build3 $(date -u +%F)" >> /opt/gab/layers
