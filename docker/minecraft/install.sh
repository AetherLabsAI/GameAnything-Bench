#!/usr/bin/env bash
set -euo pipefail; umask 022
. /tmp/gab-layer/fetch.sh
# Eclipse Temurin JDK 25.0.5+8 (sha256 = Adoptium's published checksum) at /opt/jdk: the adapter's find_java() takes /opt/jdk
# (java + javac >= 25) when the mounted game folder has no launcher runtime. No Minecraft files here: /mc/main is a mount point.
fetch https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.5%2B8/OpenJDK25U-jdk_x64_linux_hotspot_25.0.5_8.tar.gz \
      68982529869257d0ee6b824ec5aa270ff8b118bb438670da0b97ebdab178d7fb /tmp/jdk.tar.gz
mkdir -p /opt/jdk && tar xzf /tmp/jdk.tar.gz -C /opt/jdk --strip-components=1 && rm /tmp/jdk.tar.gz
/opt/jdk/bin/java -version 2>&1 | grep -q 'version "25' && /opt/jdk/bin/javac -version
mkdir -p /opt/gab/licenses/temurin && cp -r /opt/jdk/legal /opt/jdk/NOTICE /opt/gab/licenses/temurin/ 2>/dev/null || true
echo "Eclipse Temurin 25.0.5+8 -- source: https://github.com/adoptium/jdk25u (tag jdk-25.0.5+8)" > /opt/gab/licenses/temurin/SOURCE.txt
# Fabric API 0.162.0+26.3 (Apache-2.0, Fabric maven; sha256 = the adapter's API_SHA256): the container has no network, so the
# adapter's install() copies it from here instead of downloading it.
mkdir -p /opt/gab/minecraft
fetch https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/0.162.0%2B26.3/fabric-api-0.162.0%2B26.3.jar \
      8626c189b702f466800b1c8ddac6203cf8a1e0bdaee75a5f9a33b18c2baa81b1 "/opt/gab/minecraft/fabric-api-0.162.0+26.3.jar"
chmod 644 /opt/gab/minecraft/*.jar
mkdir -p /mc/main && chown player:player /mc
echo "minecraft-runtime temurin-25.0.5+8 $(date -u +%F) (game files mounted at /mc/main, not in the image)" >> /opt/gab/layers
