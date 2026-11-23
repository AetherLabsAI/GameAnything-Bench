# GameAnything-Bench image: Flare 1.15 (official AppImage, extracted)
# Installed at /home/player/oss-flare/squashfs-root -- the reference install path the gab adapter expects, so it finds it and never
# downloads at trial time. Every download is pinned (sha256 / sha512 / git commit); first-run dialogs are pre-answered in
# install.sh. Licences: docker/LICENSES.md and /opt/gab/licenses/ in the image.
#   docker build -f docker/flare/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-flare .
ARG BASE=gameanything-bench-base
FROM ${BASE}
USER root
COPY docker/common/fetch.sh docker/common/gab-readable docker/flare/ /tmp/gab-layer/
RUN umask 022 && bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
USER player
LABEL org.gameanything-bench.layer="flare" org.gameanything-bench.game="flare" org.gameanything-bench.install="/home/player/oss-flare/squashfs-root" \
      org.opencontainers.image.title="GameAnything-Bench flare" \
      org.opencontainers.image.licenses="GPL-3.0-or-later; data CC-BY-SA 3.0"
