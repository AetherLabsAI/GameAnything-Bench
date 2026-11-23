# GameAnything-Bench image: Godot TPS demo a82f154 on Godot 4.7.2
# Installed at /home/player/oss-godot-tps -- the reference install path the gab adapter expects, so it finds it and never
# downloads at trial time. Every download is pinned (sha256 / sha512 / git commit); first-run dialogs are pre-answered in
# install.sh. Licences: docker/LICENSES.md and /opt/gab/licenses/ in the image.
#   docker build -f docker/godot-tps/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-godot-tps .
ARG BASE=gameanything-bench-base
FROM ${BASE}
USER root
COPY docker/common/fetch.sh docker/common/gab-readable docker/godot-tps/ /tmp/gab-layer/
RUN umask 022 && bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
USER player
LABEL org.gameanything-bench.layer="godot-tps" org.gameanything-bench.game="godot-tps" org.gameanything-bench.install="/home/player/oss-godot-tps" \
      org.opencontainers.image.title="GameAnything-Bench godot-tps" \
      org.opencontainers.image.licenses="MIT (code), CC-BY 3.0 (art, music); Godot MIT"
