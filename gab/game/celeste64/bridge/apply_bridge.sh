#!/bin/bash
# Copy the GameAnything probe bridge into a Celeste 64 source tree: Source/Probe/C64Probe.cs + one hook line in Program.cs
# (register the module when $C64_PROBE / $C64_STATE is set) + one in UI.Text (remember drawn text for the state server).
# Both hooks do nothing unless the bridge is active. Run after a mod's Source/ overlay, so a replaced file gets them again.
set -e
SRC=${1:?source tree}; HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$SRC/Source/Probe" && cp "$HERE/C64Probe.cs" "$SRC/Source/Probe/C64Probe.cs"
grep -q "C64Probe.Active" "$SRC/Source/Program.cs" || \
  sed -i 's|^\(\s*\)App.Run<Game>(|\1if (C64Probe.Active) App.Register<C64Probe>();\n\1App.Run<Game>(|' "$SRC/Source/Program.cs"
grep -q "C64Probe.Active" "$SRC/Source/Program.cs"
U="$SRC/Source/Helpers/UI.cs"
if [ -f "$U" ] && ! grep -q "C64Probe.Seen" "$U"; then
  sed -i '/public static void Text(Batcher batch, string text,/{n;s|^\(\s*\){|\1{ C64Probe.Seen(text);|}' "$U"
fi
true
