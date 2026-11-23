#!/usr/bin/env bash
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh; H=/tmp/gab-layer
# Celeste 64 v1.1.1 (ExOK/Celeste64 tag v1.1.1 = bfc7a3b) from source, with the .NET 8 SDK (Ubuntu noble dotnet-sdk-8.0, pinned
# snapshot) -- at the reference install path /home/player/oss-celeste64/{src,vanilla-src}. A first Release build (build-plain/) runs here so
# the NuGet packages are in ~/.nuget/packages and later builds (the adapter's probe build, the agent's C# mods) work offline.
# Telemetry off (DOTNET_CLI_TELEMETRY_OPTOUT=1 in the base image). No ToS dialog. ALSA null device: FMOD needs a sound device.
# LICENCE: code MIT, but Content/ is (c) Maddy Makes Games (non-commercial fan use only) and the FMOD runtime + banks are
# proprietary: this image is for PRIVATE use; it must not be published (docker/LICENSES.md).
apt-get update && apt-get install -y --no-install-recommends dotnet-sdk-8.0
R=/home/player/oss-celeste64; mkdir -p $R/inst $R/probe $R/negative
git_at https://github.com/ExOK/Celeste64.git bfc7a3ba6f35d25bd11b4c4bad749398f70034e2 $R/src refs/tags/v1.1.1
cp -a $R/src $R/vanilla-src
chown -R player:player /home/player
runuser -u player -- env HOME=/home/player DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 dotnet build $R/src -c Release -o $R/build-plain > /tmp/build.log 2>&1 || { tail -30 /tmp/build.log; exit 1; }
tail -3 /tmp/build.log; rm -f /tmp/build.log
ls /home/player/.nuget/packages
licences $R/src celeste64 ReadMe.md Source/License.txt Source/Audio/FMOD/ReadMe.md Content/Audio/ReadMe.md
echo "Celeste 64 v1.1.1 (bfc7a3ba) -- https://github.com/ExOK/Celeste64 ; PRIVATE image: Content (c) Maddy Makes Games, FMOD proprietary" > /opt/gab/licenses/celeste64/SOURCE.txt
chown -R player:player /home/player
echo "celeste64 v1.1.1 dotnet $(dotnet --version) $(date -u +%F)" >> /opt/gab/layers
