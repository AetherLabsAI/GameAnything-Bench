# GameAnything-Bench image: Xonotic 0.8.6 (official zip, without music packs)
# Installed at /home/player/oss-xonotic/Xonotic -- the reference install path the gab adapter expects, so it finds it and never
# downloads at trial time. Every download is pinned (sha256 / sha512 / git commit); first-run dialogs are pre-answered in
# install.sh. Licences: docker/LICENSES.md and /opt/gab/licenses/ in the image.
#   docker build -f docker/xonotic/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-xonotic .
ARG BASE=gameanything-bench-base
FROM ${BASE}
USER root
COPY docker/common/fetch.sh docker/common/gab-readable docker/xonotic/ /tmp/gab-layer/
RUN umask 022 && bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
USER player
LABEL org.gameanything-bench.layer="xonotic" org.gameanything-bench.game="xonotic" org.gameanything-bench.install="/home/player/oss-xonotic/Xonotic" \
      org.opencontainers.image.title="GameAnything-Bench xonotic" \
      org.opencontainers.image.licenses="GPL-3.0-or-later (engine GPL-2.0-or-later)"
