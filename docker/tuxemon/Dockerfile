# GameAnything-Bench image: Tuxemon 0.4.35 (commit 9e6258f) + pinned Python deps
# Installed at /home/player/oss-tuxemon -- the reference install path the gab adapter expects, so it finds it and never
# downloads at trial time. Every download is pinned (sha256 / sha512 / git commit); first-run dialogs are pre-answered in
# install.sh. Licences: docker/LICENSES.md and /opt/gab/licenses/ in the image.
#   docker build -f docker/tuxemon/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-tuxemon .
ARG BASE=gameanything-bench-base
FROM ${BASE}
USER root
COPY docker/common/fetch.sh docker/common/gab-readable docker/tuxemon/ /tmp/gab-layer/
RUN umask 022 && bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
USER player
LABEL org.gameanything-bench.layer="tuxemon" org.gameanything-bench.game="tuxemon" org.gameanything-bench.install="/home/player/oss-tuxemon" \
      org.opencontainers.image.title="GameAnything-Bench tuxemon" \
      org.opencontainers.image.licenses="GPL-3.0; assets CC-BY-SA / CC-BY / CC0 / OFL (ATTRIBUTIONS.md)"
