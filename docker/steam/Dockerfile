# GameAnything-Bench image "steam": the seat runtime (docker/base) + what Steam games need -- the Steam bootstrapper (Ubuntu's
# steam-installer, multiverse), 32-bit libraries, dbus, x11vnc (only for your own Steam login),
# noVNC + websockify (Ubuntu packages novnc / websockify: the browser view of Cities: Skylines' one-time Legal Disclaimer, gab/setup/citieslegal.py), the .NET 8 SDK (tModLoader
# and BepInEx mods are C#), an offline NuGet source (the trial has no network) and BepInExPack_Valheim (pinned sha256).
# NOT inside: the Steam client itself (the bootstrapper downloads it into your state dir on first start), any game file,
# any login. Both the Steam hub container (gab steam up) and the Terraria / Valheim / Cities: Skylines trial containers run this image.
#   docker build -f docker/steam/Dockerfile --build-arg BASE=gameanything-bench-base --build-arg STEAM_ACCEPT_SSA=yes -t gameanything-bench-steam .
# STEAM_ACCEPT_SSA=yes: whoever builds the image accepts the Steam Subscriber Agreement (https://store.steampowered.com/subscriber_agreement/)
# that steam-installer asks for (the user building the image accepts it); the build refuses without it.
ARG BASE=gameanything-bench-base
FROM ${BASE}
ARG STEAM_ACCEPT_SSA=no
# apt source (docker/common/apt-source.sh): APT_MIRROR (docker/build.sh default http://archive.ubuntu.com/ubuntu) or a snapshot
# day by opt-in (APT_SNAPSHOT / APT_MIRROR=snapshot); both empty = whatever the base layer chose. Recorded in the image labels.
ARG APT_SNAPSHOT=""
ARG APT_MIRROR=""
USER root
COPY docker/common/fetch.sh docker/common/apt-source.sh docker/common/gab-readable docker/steam/install.sh /tmp/gab-layer/
RUN umask 022 && STEAM_ACCEPT_SSA=${STEAM_ACCEPT_SSA} APT_SNAPSHOT=${APT_SNAPSHOT} APT_MIRROR=${APT_MIRROR} bash /tmp/gab-layer/install.sh && sh /tmp/gab-layer/gab-readable \
 && rm -rf /tmp/gab-layer /var/lib/apt/lists/*
LABEL org.gameanything-bench.steam-apt-mirror="${APT_MIRROR}" org.gameanything-bench.steam-apt-snapshot="${APT_SNAPSHOT}"
# the hub scripts (small layer: edits do not redo the packages)
COPY docker/steam/gab-steam docker/steam/gab-steam-hub docker/steam/gab-steam-relay docker/steam/zenity-shim /tmp/gab-steam/
RUN install -m 755 /tmp/gab-steam/gab-steam /tmp/gab-steam/gab-steam-hub /tmp/gab-steam/gab-steam-relay /usr/local/bin/ && mkdir -p /usr/local/lib/gab-steam \
 && install -m 755 /tmp/gab-steam/zenity-shim /usr/local/lib/gab-steam/zenity && rm -rf /tmp/gab-steam
# RestoreSources: MSBuild reads environment variables as properties, so every `dotnet build` (player, agent, any HOME) restores
# from the image's offline feed only (no network in a trial). GAB_STEAM_STATE: where the hub keeps Steam (a host dir, mounted).
ENV RestoreSources=/opt/gab/nuget/packages GAB_BEPINEX_PACK=/opt/gab/bepinexpack GAB_STEAM_STATE=/home/player/.steam-state
USER player
LABEL org.gameanything-bench.layer="steam" org.gameanything-bench.game="terraria valheim cities_skylines" \
      org.opencontainers.image.title="GameAnything-Bench steam (runtime + Steam bootstrapper; no Steam client, no games, no login)" \
      org.opencontainers.image.licenses="Ubuntu packages under their own licences (steam-installer: Valve SSA); BepInEx LGPL-2.1; gab scripts: see the repository licence"
