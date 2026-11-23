# apt_source  -- where apt gets Ubuntu packages, for docker/build.sh users and maintainers alike (sourced by install-base.sh and
# the layers that run apt-get). Default: a normal mirror, today's packages (APT_MIRROR, default http://archive.ubuntu.com/ubuntu).
# Maintainers pin a day by opt-in: APT_SNAPSHOT=20261029T000000Z, or APT_MIRROR=snapshot (= GAB_SNAPSHOT_DAY below).
# A layer whose APT_MIRROR / APT_SNAPSHOT are both empty keeps what the layer below chose (APT_KEEP=1).
# The choice is written to /opt/gab/apt-source (and the Dockerfiles put the build args into image labels).
GAB_SNAPSHOT_DAY=20261029T000000Z
apt_source() {
  local f=/etc/apt/sources.list.d/ubuntu.sources src
  [ -f "$f" ] || return 0
  if [ -z "${APT_MIRROR:-}${APT_SNAPSHOT:-}" ] && [ "${APT_KEEP:-0}" = 1 ]; then return 0; fi
  if [ -n "${APT_SNAPSHOT:-}" ] || [ "${APT_MIRROR:-}" = snapshot ]; then
    src="https://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT:-$GAB_SNAPSHOT_DAY}/"
  else
    src="${APT_MIRROR:-http://archive.ubuntu.com/ubuntu}"; src="${src%/}/"
  fi
  sed -i -E "s#^URIs: .*#URIs: ${src}#" "$f"          # every stanza (release, updates, security) from that one source
  mkdir -p /opt/gab && echo "$src" > /opt/gab/apt-source
  echo "apt source: $src"
  # https without a CA bundle (bare image): the first index fetch skips TLS peer verification; apt still checks every index
  # against Ubuntu's signing key
  case "$src" in https://*) if [ ! -s /etc/ssl/certs/ca-certificates.crt ]; then
    apt-get -o Acquire::Retries=8 -o Acquire::https::Verify-Peer=false update
    apt-get -o Acquire::Retries=8 -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates; fi;; esac
}
