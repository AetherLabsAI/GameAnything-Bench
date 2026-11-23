#!/bin/bash
# run.sh <savedir> [colobot args...]   -- runs the distro colobot 0.2.1 from this install
D=/home/player/oss-colobot
export LD_LIBRARY_PATH=$D/root/usr/lib/x86_64-linux-gnu:$D/root/usr/lib/colobot
S=$1; shift; mkdir -p $S
exec $D/root/usr/games/colobot -datadir $D/root/usr/share/games/colobot -langdir $D/root/usr/share/locale -savedir $S "$@"
