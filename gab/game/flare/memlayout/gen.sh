#!/bin/bash
# Regenerates offsets.json (member offsets flmon.py uses) from the flare-engine v1.15 sources on the host:
#   gen.sh /path/to/flare-engine-v1.15/src      (g++ with the default libstdc++ C++11 ABI; SDL headers are stubbed: stub/)
# Symbols / vtables (flmon.SYM, flmon.VTABLES): nm -C <AppImage>/usr/bin/flare | grep -E ' [BV] (pc|camp|...)'.
# offsets.json is checked by hand against flmon.O (tests/test_game_flare.py checks they agree).
set -e; H=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d)
g++ -std=c++11 -fno-access-control -Wno-invalid-offsetof -w -I "$H/stub" -I "$1" "$H/offsets.cpp" -o "$T/offsets"
"$T/offsets" > "$H/offsets.json"; rm -rf "$T"; echo "wrote $H/offsets.json"
