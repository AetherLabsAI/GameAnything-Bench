#!/usr/bin/env bash
# Build the GameAnything-Bench images locally with your own Docker (the published ones are private for now).
#   docker/build.sh <game>... | all          from the gameanything-bench folder; base first, then each game on it
#   PLAYER_UID=$(id -u) docker/build.sh ...  the game user's uid (default 1000; only matters if you mount your own files)
#   TAG=local                                image tag (default local); then: gab setup --set docker_image "" --set docker_tag local
#   STEAM_ACCEPT_SSA=yes docker/build.sh steam   the Steam image (Terraria, Valheim, the Steam hub): you accept Valve's Steam
#                                            Subscriber Agreement for steam-installer; the image has no Steam client, game or login
#   APT_MIRROR=URL (default http://archive.ubuntu.com/ubuntu)   where apt gets packages: today's versions from a normal mirror
#   APT_SNAPSHOT=20261029T000000Z or APT_MIRROR=snapshot        maintainers only: pin apt to one day of snapshot.ubuntu.com
#   (the choice is in the image labels org.gameanything-bench.apt-*; non-apt downloads are always sha256-checked)
set -euo pipefail
cd "$(dirname "$0")/.."
TAG=${TAG:-local}; REG=${REGISTRY:-ghcr.io/aetherlabsai}
APT_MIRROR=${APT_MIRROR:-http://archive.ubuntu.com/ubuntu}; APT_SNAPSHOT=${APT_SNAPSHOT:-}
APT=(--build-arg APT_MIRROR=$APT_MIRROR --build-arg APT_SNAPSHOT=$APT_SNAPSHOT)
GAMES=("$@"); [ "${1:-}" = all ] && GAMES=(voxelibre xonotic godot-tps flare tuxemon tof2 colobot celeste64 supertuxkart stardew_valley minecraft)
[ ${#GAMES[@]} -gt 0 ] || { echo "usage: docker/build.sh <game>... | all" >&2; exit 2; }
OSS=(); for g in "${GAMES[@]}"; do [ "$g" = steam ] || OSS+=("$g"); done
if [ ${#OSS[@]} -gt 0 ]; then
  docker build -f docker/base/Dockerfile --build-arg PLAYER_UID=${PLAYER_UID:-1000} "${APT[@]}" -t $REG/gameanything-bench-base:$TAG .
  for g in "${OSS[@]}"; do docker build -f docker/$g/Dockerfile --build-arg BASE=$REG/gameanything-bench-base:$TAG "${APT[@]}" -t $REG/gameanything-bench-$g:$TAG .; done
fi
# steam: on the shared uid-1000 base (cached with the open-source images), then a thin last layer that gives the game user YOUR
# host uid / gid (docker/steam/Dockerfile.uid): Steam's login token and library land in your state dir and must belong to you
if [ ${#OSS[@]} -lt ${#GAMES[@]} ]; then
  docker image inspect $REG/gameanything-bench-base:$TAG >/dev/null 2>&1 || docker build -f docker/base/Dockerfile "${APT[@]}" -t $REG/gameanything-bench-base:$TAG .
  docker build -f docker/steam/Dockerfile --build-arg BASE=$REG/gameanything-bench-base:$TAG --build-arg STEAM_ACCEPT_SSA=${STEAM_ACCEPT_SSA:-no} "${APT[@]}" \
    -t $REG/gameanything-bench-steam-common:$TAG .
  docker build -f docker/steam/Dockerfile.uid --build-arg STEAM_BASE=$REG/gameanything-bench-steam-common:$TAG \
    --build-arg PLAYER_UID=${STEAM_PLAYER_UID:-$(id -u)} --build-arg PLAYER_GID=${STEAM_PLAYER_GID:-$(id -g)} -t $REG/gameanything-bench-steam:$TAG .
fi
echo "built: ${GAMES[*]} (tag $TAG). Use them: gab setup --set docker_tag $TAG"
