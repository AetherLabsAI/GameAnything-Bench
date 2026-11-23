# GameAnything-Bench image: Stardew Valley runtime WITHOUT the game (commercial: never in an image). The user's own Linux game
# folder with SMAPI 4.5.2 installed (docker/stardew_valley/get-game.sh) is bind-mounted READ-ONLY at /lab/game by the docker
# backend (adapter docker_mounts(); host path = config stardew_game_dir / $GAB_STARDEW_GAME_DIR). The image holds what the
# adapter's install() needs offline: the .NET 8 SDK, the local NuGet feed /lab/nuget (ModBuildConfig 4.3.0 + the net6.0
# reference packs), the Content Patcher 2.9.0 source tarball (MIT, built in the container against the mounted game).
#   docker build -f docker/stardew_valley/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-stardew_valley .
ARG BASE=gameanything-bench-base
FROM ${BASE}
ARG APT_MIRROR=""
ARG APT_SNAPSHOT=""
USER root
COPY docker/common/fetch.sh docker/common/apt-source.sh docker/common/gab-readable docker/stardew_valley/install.sh /tmp/gab-layer/
# optional local copy of the Content Patcher source tarball (docker/stardew_valley/cache/, gitignored; sha256-checked like a download)
COPY docker/stardew_valley/cache/ /tmp/gab-layer/cache/
RUN umask 022 && APT_MIRROR=${APT_MIRROR} APT_SNAPSHOT=${APT_SNAPSHOT} bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && GAB_READABLE_ROOTS=/lab sh /tmp/gab-layer/gab-readable \
    && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
USER player
LABEL org.gameanything-bench.layer="stardew_valley" org.gameanything-bench.game="stardew_valley" org.gameanything-bench.install="/lab/game (bind mount, read-only)" \
      org.opencontainers.image.title="GameAnything-Bench stardew_valley (runtime only, no game files)" \
      org.opencontainers.image.licenses=".NET SDK MIT; ModBuildConfig MIT; Content Patcher source MIT; no game files"
LABEL org.gameanything-bench.stardew-apt-mirror="${APT_MIRROR}" org.gameanything-bench.stardew-apt-snapshot="${APT_SNAPSHOT}"
