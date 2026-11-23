# GameAnything-Bench image: the RUNTIME for Minecraft Java Edition 26.3 + Fabric (Eclipse Temurin JDK 25 at /opt/jdk). NO game files:
# Minecraft is commercial; the user's own copy (a .minecraft-style folder: versions/26.3, versions/fabric-26.3-0.19.5, libraries/,
# assets/; docker/minecraft/fetch_game.py lays it out from Mojang's / Fabric's public endpoints) is bind-mounted READ ONLY at
# /mc/main by the docker backend (config minecraft_dir / GAB_MINECRAFT_DIR; gab/game/minecraft/adapter.py docker_mounts()).
# The JDK (not just a JRE) because the adapter javac-builds the harness mod and the deliverable's Fabric source projects.
#   docker build -f docker/minecraft/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-minecraft .
ARG BASE=gameanything-bench-base
FROM ${BASE}
USER root
COPY docker/common/fetch.sh docker/common/gab-readable docker/minecraft/install.sh /tmp/gab-layer/
RUN umask 022 && bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
ENV GAB_MINECRAFT_DIR=/mc/main
USER player
LABEL org.gameanything-bench.layer="minecraft" org.gameanything-bench.game="minecraft" org.gameanything-bench.install="/mc/main (mounted, not in the image)" \
      org.opencontainers.image.title="GameAnything-Bench minecraft runtime (no game files)" \
      org.opencontainers.image.licenses="Temurin JDK GPL-2.0 with Classpath Exception; the game is NOT included (bring your own copy)"
