#!/usr/bin/env bash
# Layer "steam" (docker/steam/Dockerfile). See the Dockerfile header for what is in and what is NOT.
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh; H=/tmp/gab-layer
[ "${STEAM_ACCEPT_SSA:-no}" = yes ] || { echo "refusing to install Steam: build with --build-arg STEAM_ACCEPT_SSA=yes (you accept the Steam Subscriber Agreement)" >&2; exit 1; }
# apt source (APT_MIRROR / APT_SNAPSHOT; both empty: keep the base layer's), multiverse (steam-installer) + i386
. $H/apt-source.sh; APT_KEEP=1 apt_source
sed -i -E 's/^Components: .*/Components: main restricted universe multiverse/' /etc/apt/sources.list.d/ubuntu.sources
dpkg --add-architecture i386
echo steam steam/question select "I AGREE" | debconf-set-selections
echo steam steam/license note '' | debconf-set-selections
apt-get -o Acquire::Retries=8 update
apt-get -o Acquire::Retries=8 install -y --no-install-recommends \
  steam-installer steam-libs-amd64 steam-libs-i386:i386 \
  libgl1:i386 libgl1-mesa-dri:i386 libglx-mesa0:i386 libegl1:i386 libvulkan1:i386 mesa-vulkan-drivers:i386 \
  libnm0 libgtk2.0-0t64 libva2 libvdpau1 libnss3 libbz2-1.0 libxss1 libxcomposite1 libxdamage1 libatk1.0-0t64 libatk-bridge2.0-0t64 \
  libcups2t64 libgbm1 libdrm2 libxshmfence1 libpango-1.0-0 libcairo2 \
  dbus dbus-x11 zenity xdg-utils x11vnc lsof novnc websockify \
  dotnet-sdk-8.0
# offline NuGet source: the packages the harness / agent builds need, restored once here into /opt/gab/nuget/packages (that folder
# layout is a valid local feed; RestoreSources in the image points every restore at it). net462 = BepInEx plugins (the SDK adds
# Microsoft.NETFramework.ReferenceAssemblies on Linux); tModLoader.CodeAssist = tModLoader's mod targets.
export NUGET_PACKAGES=/opt/gab/nuget/packages DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 HOME=/tmp/nuget-home
mkdir -p $NUGET_PACKAGES /tmp/warm/a /tmp/warm/b
cat > /tmp/warm/a/a.csproj <<'X'
<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net462</TargetFramework></PropertyGroup></Project>
X
cat > /tmp/warm/b/b.csproj <<'X'
<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>
<ItemGroup><PackageReference Include="tModLoader.CodeAssist" Version="0.1.5" /></ItemGroup></Project>
X
echo 'class A {}' > /tmp/warm/a/A.cs; echo 'class B {}' > /tmp/warm/b/B.cs
for p in a b; do dotnet build /tmp/warm/$p -nologo -v q -p:RestoreSources=https://api.nuget.org/v3/index.json; done
# the same build offline, from the local feed only (what a trial does)
rm -rf /tmp/warm/*/obj /tmp/warm/*/bin /tmp/nuget-home; mkdir -p /tmp/nuget-home
NUGET_PACKAGES=/tmp/nuget-home/pk dotnet build /tmp/warm/a -nologo -v q -p:RestoreSources=/opt/gab/nuget/packages
rm -rf /tmp/warm /tmp/nuget-home
# BepInExPack_Valheim (the Valheim adapter's pin): unpacked where install() looks first (GAB_BEPINEX_PACK), so a trial never downloads
fetch https://thunderstore.io/package/download/denikson/BepInExPack_Valheim/5.4.2351/ \
      bce631497976a93977ceb08e166712e6c31d15244956f89f17df092a9b62e29f /tmp/bep.zip
mkdir -p /opt/gab/bepinexpack && python3 -I -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" /tmp/bep.zip /opt/gab/bepinexpack
cp /tmp/bep.zip /opt/gab/bepinexpack/BepInExPack_Valheim-5.4.2351.zip; rm -f /tmp/bep.zip
test -f /opt/gab/bepinexpack/BepInExPack_Valheim/BepInEx/core/BepInEx.dll
mkdir -p /opt/gab/licenses/steam && echo "steam-installer (Ubuntu multiverse): Valve Steam Subscriber Agreement, accepted at build time (STEAM_ACCEPT_SSA=yes)" > /opt/gab/licenses/steam/NOTE.txt
echo "steam $(dpkg-query -W -f '${Version}' steam-installer) dotnet $(dotnet --version) bepinexpack 5.4.2351 $(date -u +%F)" >> /opt/gab/layers
