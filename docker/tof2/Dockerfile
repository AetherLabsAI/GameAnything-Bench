# GameAnything-Bench image: Tanks of Freedom II dbe629e (1.0.9) on Godot 4.4.1
# Installed at /home/player/oss-tof2 -- the reference install path the gab adapter expects, so it finds it and never
# downloads at trial time. Every download is pinned (sha256 / sha512 / git commit); first-run dialogs are pre-answered in
# install.sh. Licences: docker/LICENSES.md and /opt/gab/licenses/ in the image.
#   docker build -f docker/tof2/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-tof2 .
ARG BASE=gameanything-bench-base
FROM ${BASE}
USER root
COPY docker/common/fetch.sh docker/common/gab-readable docker/tof2/ /tmp/gab-layer/
RUN umask 022 && bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
USER player
LABEL org.gameanything-bench.layer="tof2" org.gameanything-bench.game="tof2" org.gameanything-bench.install="/home/player/oss-tof2" \
      org.opencontainers.image.title="GameAnything-Bench tof2" \
      org.opencontainers.image.licenses="MIT; music/sfx CC BY-SA 4.0 / CC BY 4.0"
