# fetch URL HASH OUT  -- download with retries and check the hash before anything uses the file.
#   HASH: 64 hex = sha256, 128 hex = sha512 (the algorithm the upstream publishes). A mismatch stops the build.
fetch() {
  local url=$1 want=$2 out=$3 got
  curl -fsSL --retry 5 --retry-delay 5 -o "$out" "$url"
  case ${#want} in 64) got=$(sha256sum "$out" | cut -d' ' -f1);; 128) got=$(sha512sum "$out" | cut -d' ' -f1);;
    *) echo "fetch: bad hash for $url" >&2; return 1;; esac
  if [ "$got" != "$want" ]; then echo "fetch: HASH MISMATCH for $url: $got" >&2; rm -f "$out"; return 1; fi
  echo "fetch: ok $(basename "$out") ($(du -h "$out" | cut -f1))"
}
# git_at URL COMMIT DIR [REF]  -- exactly one commit (the commit id is the pin), no history; REF (tag / branch) is the fallback
#   for servers that refuse a fetch by commit id; the checked-out commit must still equal COMMIT
git_at() {
  local url=$1 commit=$2 dir=$3 ref=${4:-}
  mkdir -p "$dir" && git -C "$dir" init -q && git -C "$dir" remote add origin "$url"
  git -C "$dir" -c protocol.version=2 fetch -q --depth 1 origin "$commit" || { [ -n "$ref" ] && git -C "$dir" fetch -q --depth 1 origin "$ref"; }
  git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
  [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ] || { echo "git_at: $url is not at $commit" >&2; return 1; }
  rm -rf "$dir/.git"; echo "git_at: ok $url @ $commit"
}
# licences DIR NAME FILES...  -- keep the licence / credit files of a component under /opt/gab/licenses/NAME
licences() { local src=$1 name=$2; shift 2; mkdir -p /opt/gab/licenses/$name
  for f in "$@"; do if [ -e "$src/$f" ]; then cp -r "$src/$f" /opt/gab/licenses/$name/; else echo "licences: $name: $f missing" >&2; fi; done; }
