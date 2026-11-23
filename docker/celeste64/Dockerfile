# GameAnything-Bench image: Celeste 64 v1.1.1 (source, .NET 8 SDK; PRIVATE ONLY: content not openly licensed, bundles FMOD)
# Installed at /home/player/oss-celeste64 -- the reference install path the gab adapter expects, so it finds it and never
# downloads at trial time. Every download is pinned (sha256 / sha512 / git commit); first-run dialogs are pre-answered in
# install.sh. Licences: docker/LICENSES.md and /opt/gab/licenses/ in the image.
#   docker build -f docker/celeste64/Dockerfile --build-arg BASE=gameanything-bench-base -t gameanything-bench-celeste64 .
ARG BASE=gameanything-bench-base
FROM ${BASE}
USER root
COPY docker/common/fetch.sh docker/common/gab-readable docker/celeste64/ /tmp/gab-layer/
RUN umask 022 && bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
USER player
LABEL org.gameanything-bench.layer="celeste64" org.gameanything-bench.game="celeste64" org.gameanything-bench.install="/home/player/oss-celeste64" \
      org.opencontainers.image.title="GameAnything-Bench celeste64" \
      org.opencontainers.image.licenses="code MIT; Content (c) Maddy Makes Games; FMOD proprietary"
