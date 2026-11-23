# Download tool only (docker/stardew_valley/get-game.sh), never a trial image: Valve's SteamCMD needs 32-bit glibc / libgcc, which
# the host does not have (and gets no sudo for). The SteamCMD files, its cache and the downloaded game live in host folders
# bind-mounted at run time (/steam, /game); this image holds only the 32-bit runtime. No accounts, no keys, no game files.
#   docker build -f docker/stardew_valley/steamcmd.Dockerfile -t gab-steamcmd:local docker/stardew_valley
ARG UBUNTU=ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
FROM ${UBUNTU}
ENV LANG=C.UTF-8 TZ=UTC DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates lib32gcc-s1 lib32stdc++6 libc6-i386 curl \
    && rm -rf /var/lib/apt/lists/* && mkdir -p /steam /game && chmod 1777 /steam /game
WORKDIR /steam
LABEL org.gameanything-bench.layer="steamcmd-runtime" org.opencontainers.image.title="GameAnything-Bench SteamCMD runtime (download tool, no game)"
