#!/usr/bin/env bash
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh; H=/tmp/gab-layer
# SuperTuxKart 1.5 (official Linux x86_64 build, sha256 = GitHub's asset digest) at
# /home/player/oss-supertuxkart/SuperTuxKart-1.5-linux-x86_64 (the reference install path). First-run dialogs pre-answered with the
# seed (seed_cfg/, from probes_supertuxkart.seed_cfg): enable_internet = 2 (= "No" to "may SuperTuxKart connect to a server"),
# hardware reports off, local player "Player" (no tutorial / login prompt). Also seeded into the default HOME config.
R=/home/player/oss-supertuxkart; mkdir -p $R/inst && cd $R
fetch https://github.com/supertuxkart/stk-code/releases/download/1.5/SuperTuxKart-1.5-linux-x86_64.tar.gz \
      57090b6c2163eb691f20104ae9712204acbb4e8341059dee0a5ff5315efc401b stk.tar.gz
tar xzf stk.tar.gz && rm stk.tar.gz
test -x SuperTuxKart-1.5-linux-x86_64/bin/supertuxkart
mkdir -p $R/seed_cfg /home/player/.config/supertuxkart/config-0.10
cp $H/seed/* $R/seed_cfg/ && cp $H/seed/* /home/player/.config/supertuxkart/config-0.10/
grep -q '<enable_internet value="2"' $R/seed_cfg/config.xml
mkdir -p /opt/gab/licenses/supertuxkart && cp SuperTuxKart-1.5-linux-x86_64/data/../COPYING /opt/gab/licenses/supertuxkart/ 2>/dev/null || true
(cd SuperTuxKart-1.5-linux-x86_64 && find . -maxdepth 4 \( -iname 'COPYING*' -o -iname 'licenses.txt' -o -iname 'LICENSE*' \) -type f -exec cp --parents {} /opt/gab/licenses/supertuxkart/ \;)
echo "SuperTuxKart 1.5 -- source: https://github.com/supertuxkart/stk-code/releases/tag/1.5 (SuperTuxKart-1.5-src.tar.gz)" > /opt/gab/licenses/supertuxkart/SOURCE.txt
chown -R player:player /home/player
echo "supertuxkart 1.5 $(date -u +%F)" >> /opt/gab/layers
