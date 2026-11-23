#!/usr/bin/env bash
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh; H=/tmp/gab-layer
# Tuxemon 0.4.35 (development checkout at 9e6258f, the commit the adapter was verified with) + a venv with the versions the
# reference venv had (requirements.lock). Layout: /home/player/oss-tuxemon/{src,venv}. No EULA / ToS; the game's network thread is
# switched off by the adapter.
R=/home/player/oss-tuxemon; mkdir -p $R
git_at https://github.com/Tuxemon/Tuxemon.git 9e6258ff726b786040a267e8bdbbf037b560285e $R/src
python3 -m venv $R/venv
$R/venv/bin/pip install --no-cache-dir --disable-pip-version-check -r $H/requirements.lock
$R/venv/bin/python -c "import pygame, pytmx, pyscroll, pygame_menu, pydantic, yaml; print('tuxemon deps ok', pygame.version.ver)"
licences $R/src tuxemon LICENSE ATTRIBUTIONS.md CONTRIBUTORS.md
echo "Tuxemon 9e6258ff726b786040a267e8bdbbf037b560285e -- https://github.com/Tuxemon/Tuxemon (GPL-3.0); Python packages: PyPI (venv/)" > /opt/gab/licenses/tuxemon/SOURCE.txt
chown -R player:player /home/player
echo "tuxemon 9e6258f $(date -u +%F)" >> /opt/gab/layers
