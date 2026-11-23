#!/usr/bin/env bash
# Layer "base" of every GameAnything-Bench game image (docker/base/Dockerfile). What every game adapter and the `game` / `play`
# tools expect inside a seat (the tool set the game adapters use): a private X screen (Xvfb), xdotool + x11-utils
# for real player input, ffmpeg for screenshots, Mesa OpenGL / Vulkan (llvmpipe / lavapipe; NVIDIA libraries come from the host
# with --gpus), VirtualGL (for a GPU), SDL2 / OpenAL / ALSA null output, fonts, Python 3 with PyYAML / Pillow / NumPy / requests,
# the user `player` (uid PLAYER_UID; the game runs as this user) and the user `agent` (uid AGENT_UID; an agent harness inside the
# trial container runs as this user, never as the player). NO game files, no accounts, no keys.
#   PLAYER_UID=1000  AGENT_UID=2000
#   APT_MIRROR=http://archive.ubuntu.com/ubuntu   (default) today's packages from that mirror
#   APT_SNAPSHOT=20261029T000000Z | APT_MIRROR=snapshot   maintainers: pin every apt package to one day of snapshot.ubuntu.com
set -euo pipefail
umask 022          # never inherit the builder's umask (some builders run with 077)
PLAYER_UID=${PLAYER_UID:-1000}; AGENT_UID=${AGENT_UID:-2000}
export DEBIAN_FRONTEND=noninteractive
H=/tmp/gab-layer
. $H/fetch.sh
. $H/apt-source.sh

# 0. the apt source (docker/common/apt-source.sh): a normal mirror by default, a snapshot day only by opt-in
apt_source
apt-get -o Acquire::Retries=8 update
apt-get -o Acquire::Retries=8 install -y --no-install-recommends \
  ca-certificates curl unzip zip xz-utils zstd procps psmisc less file jq git sqlite3 util-linux \
  xvfb x11-utils x11-xserver-utils xdotool xauth \
  libgl1 libgl1-mesa-dri libglu1-mesa libegl1 libgles2 libglx-mesa0 libvulkan1 mesa-vulkan-drivers mesa-utils \
  libx11-6 libxext6 libxrandr2 libxi6 libxcursor1 libxinerama1 libxss1 libxxf86vm1 libxrender1 libxtst6 libxkbcommon-x11-0 \
  libsdl2-2.0-0 libasound2t64 libpulse0 libopenal1 libfreetype6 libfontconfig1 \
  fonts-dejavu-core fonts-dejavu-extra fonts-liberation \
  ffmpeg python3 python3-venv python3-pip python3-yaml python3-pil python3-numpy python3-requests
# VirtualGL 3.1.5 (official release .deb, sha256 = GitHub's asset digest): `vglrun -d egl` renders on an NVIDIA GPU passed in with
# --gpus. The real binary lives in /opt/VirtualGL; /usr/local/bin/vglrun (docker/common/vglrun) falls back to Mesa software GL
# when the container has no GPU, so adapters written for the GPU seats run unchanged on a CPU-only Docker.
fetch https://github.com/VirtualGL/virtualgl/releases/download/3.1.5/virtualgl_3.1.5_amd64.deb \
      df3f7788ce41b182a47c0d298e5cd6d2d63579522cb41825970b7726e825485e /tmp/virtualgl.deb
apt-get install -y --no-install-recommends /tmp/virtualgl.deb && rm -f /tmp/virtualgl.deb
rm -rf /var/lib/apt/lists/*

# ALSA: a null default device (games that open sound -- FMOD, SDL -- get a silent sink instead of crashing)
install -m 644 $H/asound.conf /etc/asound.conf
install -m 755 $H/vglrun /usr/local/bin/vglrun
install -m 755 $H/gab-seat /usr/local/bin/gab-seat
install -m 755 $H/gab-readable /usr/local/bin/gab-readable

# users: player = the game, agent = an agent harness running inside the trial (gab run --agent-in-container)
for u in ubuntu; do id $u >/dev/null 2>&1 && userdel -r $u 2>/dev/null || true; done
id player >/dev/null 2>&1 || useradd -m -u "$PLAYER_UID" -s /bin/bash player
id agent  >/dev/null 2>&1 || useradd -m -u "$AGENT_UID" -s /bin/bash agent
mkdir -p /opt/gab/licenses /opt/gab-agent/bin /run/gab /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
chown player:player /home/player
echo "base $(date -u +%F) player=$PLAYER_UID agent=$AGENT_UID apt=$(cat /opt/gab/apt-source 2>/dev/null)" > /opt/gab/layers
