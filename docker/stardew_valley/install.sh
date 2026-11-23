#!/usr/bin/env bash
set -euo pipefail; umask 022; export DEBIAN_FRONTEND=noninteractive
. /tmp/gab-layer/fetch.sh
. /tmp/gab-layer/apt-source.sh; APT_KEEP=1 apt_source     # APT_MIRROR / APT_SNAPSHOT, else the base layer's source
# Stardew Valley layer: NO game files. /lab/game = mount point of the user's game folder (read-only bind mount at docker run);
# the adapter copies it into the seat at install() (gab/game/stardew_valley/adapter.py) and builds GabBridge + Content Patcher there.
# .NET 8 SDK (Ubuntu noble dotnet-sdk-8.0, from the apt source above): builds the harness mod, Content Patcher and the agent's C# mods (net6.0).
apt-get -o Acquire::Retries=8 update && apt-get -o Acquire::Retries=8 install -y --no-install-recommends dotnet-sdk-8.0
# Local NuGet feed (offline builds; the adapter's nuget.config lists only this folder): what a net6.0 SMAPI mod restores
NF=https://api.nuget.org/v3-flatcontainer; F=/lab/nuget; mkdir -p $F /lab/game
fetch $NF/pathoschild.stardew.modbuildconfig/4.3.0/pathoschild.stardew.modbuildconfig.4.3.0.nupkg \
      354b7d1bd4d4eb68caf5a5f3ef3508adaed705a9de742fa8e3f09ce7af02b721 $F/pathoschild.stardew.modbuildconfig.4.3.0.nupkg
fetch $NF/microsoft.netcore.app.ref/6.0.36/microsoft.netcore.app.ref.6.0.36.nupkg \
      f4b66056820517ca8dc94bbc91d26b6062eeb4c17f70bd8af361cddb2c30971f $F/microsoft.netcore.app.ref.6.0.36.nupkg
fetch $NF/microsoft.aspnetcore.app.ref/6.0.36/microsoft.aspnetcore.app.ref.6.0.36.nupkg \
      f630e459b8f0fe777ccaa7735536a00aeaabea8c09fc350c8b80655194f88565 $F/microsoft.aspnetcore.app.ref.6.0.36.nupkg
# Content Patcher 2.9.0 source (GitHub tag content-patcher/2.9.0, MIT): where the adapter's _install_cp() looks before downloading
R=/home/player/gab-stardew; mkdir -p $R/dl; CPT=StardewMods-content-patcher-2.9.0.tar.gz
CPS=02320a7b9f54b4ce60c74a3faf2d158c24e0d0c0191774275172c7005cd0b42f
if [ -f /tmp/gab-layer/cache/$CPT ] && echo "$CPS  /tmp/gab-layer/cache/$CPT" | sha256sum -c --quiet -; then
  cp /tmp/gab-layer/cache/$CPT $R/dl/$CPT; echo "content patcher source: local cache (sha256 ok)"
else   # codeload can be slow: several tries, each bounded
  for i in 1 2 3 4 5; do
    curl -fsSL --connect-timeout 30 --max-time 900 -A "Mozilla/5.0 (X11; Linux x86_64)" -o $R/dl/$CPT \
         https://codeload.github.com/Pathoschild/StardewMods/tar.gz/refs/tags/content-patcher/2.9.0 && break; sleep 10
  done
fi
echo "$CPS  $R/dl/$CPT" | sha256sum -c -
# offline restore check: a net6.0 project with ModBuildConfig restores from /lab/nuget alone (no nuget.org)
T=/tmp/sdrestore; mkdir -p $T/p
cat > $T/p/p.csproj <<'X'
<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net6.0</TargetFramework></PropertyGroup>
<ItemGroup><PackageReference Include="Pathoschild.Stardew.ModBuildConfig" Version="4.3.0" /></ItemGroup></Project>
X
printf '<?xml version="1.0" encoding="utf-8"?>\n<configuration><packageSources><clear /><add key="local" value="%s" /></packageSources></configuration>\n' $F > $T/p/nuget.config
HOME=$T DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 dotnet restore $T/p --packages $T/pk
rm -rf $T /tmp/MSBuildTemp* /tmp/NuGetScratch* /tmp/.dotnet; find /tmp -mindepth 1 -maxdepth 1 -user root ! -name .X11-unix ! -name gab-layer -exec rm -rf {} +
mkdir -p /opt/gab/licenses/stardew_valley
cat > /opt/gab/licenses/stardew_valley/SOURCE.txt <<'X'
No Stardew Valley files are in this image (commercial game, (c) ConcernedApe): the user's own copy is mounted read-only at /lab/game.
Pathoschild.Stardew.ModBuildConfig 4.3.0 (MIT, nuget.org), Microsoft.NETCore.App.Ref / Microsoft.AspNetCore.App.Ref 6.0.36 (MIT, nuget.org)
Content Patcher 2.9.0 source: https://github.com/Pathoschild/StardewMods tag content-patcher/2.9.0 (MIT), sha256 02320a7b...cd0b42f
X
chown -R player:player /home/player
echo "stardew_valley runtime (no game) dotnet $(dotnet --version) $(date -u +%F)" >> /opt/gab/layers
