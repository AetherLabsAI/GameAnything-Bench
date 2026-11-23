# GameAnything-Bench image: VoxeLibre 0.92.4 on Luanti 5.17.0
# Installed at /home/player/oss-voxelibre -- the reference install path the gab adapter expects, so it finds it and never
# downloads at trial time. Every download is pinned (sha256 / sha512 / git commit); first-run dialogs are pre-answered in
# install.sh. Licences: docker/LICENSES.md and /opt/gab/licenses/ in the image.
#   docker build -f docker/voxelibre/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-voxelibre .
ARG BASE=gameanything-bench-base
FROM ${BASE}
USER root
COPY docker/common/fetch.sh docker/common/gab-readable docker/voxelibre/ /tmp/gab-layer/
RUN umask 022 && bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
USER player
LABEL org.gameanything-bench.layer="voxelibre" org.gameanything-bench.game="voxelibre" org.gameanything-bench.install="/home/player/oss-voxelibre" \
      org.opencontainers.image.title="GameAnything-Bench voxelibre" \
      org.opencontainers.image.licenses="VoxeLibre GPL-3.0-or-later + CC BY-SA; Luanti LGPL-2.1-or-later + CC BY-SA"
