# GameAnything-Bench, shared layer "base": every game image (docker/<game>/Dockerfile) starts here. Ubuntu 24.04 pinned by digest,
# apt from a normal mirror (APT_MIRROR; maintainers may pin a snapshot day: APT_SNAPSHOT), other downloads checked by sha256. Seat runtime only: Xvfb, xdotool,
# ffmpeg, Mesa + VirtualGL, SDL2 / OpenAL / ALSA null, fonts, Python 3; users player (the game) and agent (an in-container agent).
# NO game files, no accounts, no keys.     Build (from the gameanything-bench folder):
#   docker build -f docker/base/Dockerfile -t gameanything-bench-base .        (docker/build.sh does base + a game)
ARG UBUNTU=ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
FROM ${UBUNTU}
ARG PLAYER_UID=1000
ARG AGENT_UID=2000
ARG APT_SNAPSHOT=""
ARG APT_MIRROR=http://archive.ubuntu.com/ubuntu
ENV LANG=C.UTF-8 TZ=UTC DEBIAN_FRONTEND=noninteractive
COPY docker/common/ /tmp/gab-layer/
RUN umask 022 && PLAYER_UID=${PLAYER_UID} AGENT_UID=${AGENT_UID} APT_SNAPSHOT=${APT_SNAPSHOT} APT_MIRROR=${APT_MIRROR} bash /tmp/gab-layer/install-base.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer
ENV HOME=/home/player DISPLAY=:101 GAB_SCREEN=1024x768x24 GAB_IN_CONTAINER=1 NVIDIA_DRIVER_CAPABILITIES=all \
    SDL_AUDIODRIVER=dummy DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
USER player
WORKDIR /home/player
CMD ["/usr/local/bin/gab-seat"]
LABEL org.gameanything-bench.layer="base" org.gameanything-bench.player-uid="${PLAYER_UID}" org.gameanything-bench.agent-uid="${AGENT_UID}" \
      org.gameanything-bench.apt-snapshot="${APT_SNAPSHOT}" org.gameanything-bench.apt-mirror="${APT_MIRROR}" \
      org.opencontainers.image.source="https://github.com/AetherLabsAI/GameAnything-Bench" \
      org.opencontainers.image.title="GameAnything-Bench seat base (runtime only, no game)" \
      org.opencontainers.image.licenses="Ubuntu packages under their own licences; VirtualGL LGPL-2.1 / wxWindows; gab scripts: see the repository licence"
