# GameAnything-Bench image: Colobot: Gold Edition 0.2.1 (Ubuntu noble packages, unpacked)
# Installed at /home/player/oss-colobot -- the reference install path the gab adapter expects, so it finds it and never
# downloads at trial time. Every download is pinned (sha256 / sha512 / git commit); first-run dialogs are pre-answered in
# install.sh. Licences: docker/LICENSES.md and /opt/gab/licenses/ in the image.
#   docker build -f docker/colobot/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-colobot .
ARG BASE=gameanything-bench-base
FROM ${BASE}
USER root
COPY docker/common/fetch.sh docker/common/gab-readable docker/colobot/ /tmp/gab-layer/
RUN umask 022 && bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
USER player
LABEL org.gameanything-bench.layer="colobot" org.gameanything-bench.game="colobot" org.gameanything-bench.install="/home/player/oss-colobot" \
      org.opencontainers.image.title="GameAnything-Bench colobot" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"
