"""Cities: Skylines 1 settings files (*.cgs, ColossalFramework.SettingsFile v4) and the mod on/off switches inside them.

A local mod is enabled by the bool `<mod folder name><hash>.enabled` in userGameState.cgs, where hash is Mono's
String.GetHashCode of the mod's full path AS THE GAME SEES IT (inside the container), e.g.
"WCHello595075863.enabled" for /home/player/.local/share/Colossal Order/Cities_Skylines/Addons/Mods/WCHello (verified
against a file written by the game). New mods are off by default, so the harness switches the bridge and the mod under test
on in each instance's userGameState.cgs before the game starts (no UI clicks).

Format (little endian): "CGSF", uint16 version (4), then six sections, each int32 count + entries of
(7-bit-length-prefixed UTF-8 key, value): int32, bool (1 byte), float32, string, input key (int32), int64.

    python cgs.py <file.cgs>                                  print the settings
    python cgs.py <file.cgs> --enable "<container path of a mod dir>" ...
"""
import struct, sys
from pathlib import Path

KINDS = ("int", "bool", "float", "string", "inputkey", "long")


def mono_hash(s: str) -> int:
    """Mono 2.x String.GetHashCode (the game's runtime): h = h*31 + c over UTF-16 code units, two at a time."""
    units = s.encode("utf-16-le"); cs = [units[i] | units[i + 1] << 8 for i in range(0, len(units), 2)]
    h = 0
    for c in cs: h = (h * 31 + c) & 0xFFFFFFFF
    return h - (1 << 32) if h >= 1 << 31 else h


def _rstr(b, i):
    n = 0; shift = 0
    while True:
        x = b[i]; i += 1; n |= (x & 0x7F) << shift; shift += 7
        if not x & 0x80: break
    return b[i:i + n].decode("utf-8"), i + n


def _wstr(s):
    d = s.encode("utf-8"); n = len(d); out = bytearray()
    while True:
        x = n & 0x7F; n >>= 7
        out.append(x | (0x80 if n else 0))
        if not n: break
    return bytes(out) + d


def read(path) -> dict:
    b = Path(path).read_bytes()
    if b[:4] != b"CGSF": raise ValueError(f"{path}: not a CGSF settings file")
    ver = struct.unpack_from("<H", b, 4)[0]; i = 6
    out = {"_version": ver}
    for kind in KINDS:
        if i >= len(b): out[kind] = {}; continue
        n = struct.unpack_from("<i", b, i)[0]; i += 4; sec = {}
        for _ in range(n):
            k, i = _rstr(b, i)
            if kind in ("int", "inputkey"): v = struct.unpack_from("<i", b, i)[0]; i += 4
            elif kind == "bool": v = bool(b[i]); i += 1
            elif kind == "float": v = struct.unpack_from("<f", b, i)[0]; i += 4
            elif kind == "string": v, i = _rstr(b, i)
            else: v = struct.unpack_from("<q", b, i)[0]; i += 8
            sec[k] = v
        out[kind] = sec
    return out


def write(path, d: dict):
    b = bytearray(b"CGSF") + struct.pack("<H", d.get("_version", 4))
    for kind in KINDS:
        sec = d.get(kind) or {}; b += struct.pack("<i", len(sec))
        for k, v in sec.items():
            b += _wstr(k)
            if kind in ("int", "inputkey"): b += struct.pack("<i", int(v))
            elif kind == "bool": b += bytes([1 if v else 0])
            elif kind == "float": b += struct.pack("<f", float(v))
            elif kind == "string": b += _wstr(str(v))
            else: b += struct.pack("<q", int(v))
    tmp = Path(str(path) + ".tmp"); tmp.write_bytes(bytes(b)); tmp.replace(path)


def mod_key(container_mod_dir: str) -> str:
    p = container_mod_dir.rstrip("/")
    return f"{p.rsplit('/', 1)[1]}{mono_hash(p)}.enabled"


def enable_mods(path, container_mod_dirs, on=True):
    """Set the enabled switch of each mod (container paths of the mod folders) in userGameState.cgs (created if missing)."""
    path = Path(path)
    d = read(path) if path.exists() else {"_version": 4, **{k: {} for k in KINDS}}
    for m in container_mod_dirs: d["bool"][mod_key(m)] = bool(on)
    write(path, d)
    return [mod_key(m) for m in container_mod_dirs]


if __name__ == "__main__":
    f = sys.argv[1]
    if "--enable" in sys.argv: print(enable_mods(f, sys.argv[sys.argv.index("--enable") + 1:]))
    else:
        import json; print(json.dumps(read(f), indent=1))
