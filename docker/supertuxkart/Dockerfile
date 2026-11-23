# GameAnything-Bench image: SuperTuxKart 1.5 (official Linux x86_64 build)
# Installed at /home/player/oss-supertuxkart -- the reference install path the gab adapter expects, so it finds it and never
# downloads at trial time. Every download is pinned (sha256 / sha512 / git commit); first-run dialogs are pre-answered in
# install.sh. Licences: docker/LICENSES.md and /opt/gab/licenses/ in the image.
#   docker build -f docker/supertuxkart/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-supertuxkart .
ARG BASE=gameanything-bench-base
FROM ${BASE}
USER root
COPY docker/common/fetch.sh docker/common/gab-readable docker/supertuxkart/ /tmp/gab-layer/
RUN umask 022 && bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
USER player
LABEL org.gameanything-bench.layer="supertuxkart" org.gameanything-bench.game="supertuxkart" org.gameanything-bench.install="/home/player/oss-supertuxkart" \
      org.opencontainers.image.title="GameAnything-Bench supertuxkart" \
      org.opencontainers.image.licenses="GPL-3.0-or-later; data CC / GPL / public domain (data/**/licenses.txt)"
